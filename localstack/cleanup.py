"""Reviewable, engine-bound cleanup. Never prunes Docker or guesses shared ownership."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'business'), str(ROOT / 'scripts')]
from business import Lab, pipe, publisher_schedule
from kube import DOCKER, run

ENDPOINT = 'http://127.0.0.1:4566'
PREFIX = 'micro-test-local-'
BUILTIN_NETWORKS = {'bridge', 'host', 'none'}
FILE_ROOTS = ('.state', '.venv', 'reports')


def report_path(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('Plan/report path must not traverse symlinks')
    if path.is_relative_to(ROOT.parent) or path.suffix != '.json':
        raise ValueError('Save cleanup JSON outside the checkout (e.g. $HOME/lab-cleanup.json)')
    return path


def write(path, value):
    path = report_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')
    path.chmod(0o600)


def require_wsl():
    if (sys.platform != 'linux' or not Path('/proc/sys/kernel/osrelease').exists()
            or 'microsoft' not in Path('/proc/sys/kernel/osrelease').read_text().lower()):
        raise ValueError('Docker cleanup must run inside laptop WSL, never through remote Docker')
    os.environ.pop('DOCKER_CONTEXT', None)
    os.environ['DOCKER_HOST'] = 'unix:///var/run/docker.sock'


def inventory():
    info = json.loads(run(DOCKER + ['info', '--format', '{{json .}}']).stdout)
    if info.get('OSType') != 'linux' or not info.get('ID'):
        raise ValueError('Identifiable local Linux Docker engine required')
    def items(kind, args):
        ids = run(DOCKER + args).stdout.split()
        return json.loads(run(DOCKER + [kind, 'inspect'] + sorted(set(ids))).stdout) if ids else []
    return {'engine': info['ID'],
            'containers': items('container', ['ps', '-aq', '--no-trunc']),
            'networks': items('network', ['network', 'ls', '-q', '--no-trunc']),
            'volumes': items('volume', ['volume', 'ls', '-q']),
            'images': items('image', ['image', 'ls', '-aq', '--no-trunc'])}


def cloud_facts(lab):
    """Read ownership only; never dump secrets, payloads or container environments."""
    cf = lab.client('cloudformation')
    stacks = []
    for page in cf.get_paginator('describe_stacks').paginate():
        stacks.extend(page['Stacks'])
    if any(not s['StackName'].startswith(PREFIX) or {'Key': 'Lab', 'Value': 'micro-test-local'}
           not in s.get('Tags', []) for s in stacks):
        raise ValueError('Selected emulator has foreign CloudFormation stacks; whole-emulator cleanup refused')
    functions, clusters = set(), set()
    for stack in stacks:
        for page in cf.get_paginator('list_stack_resources').paginate(StackName=stack['StackName']):
            for resource in page['StackResourceSummaries']:
                kind, name = resource['ResourceType'], resource.get('PhysicalResourceId', '')
                if kind == 'AWS::Lambda::Function': functions.add(name)
                if kind == 'AWS::EKS::Cluster': clusters.add(name)
    instances = set()
    for page in lab.client('ec2').get_paginator('describe_instances').paginate():
        for reservation in page['Reservations']:
            for item in reservation['Instances']:
                tags = {t['Key']: t['Value'] for t in item.get('Tags', [])}
                owned_eks = any(tags.get('eks:cluster-name') == cluster and
                                tags.get('kubernetes.io/cluster/' + cluster) == 'owned' for cluster in clusters)
                if owned_eks or tags.get('Lab') == 'micro-test-local':
                    instances.add(item['InstanceId'])
    return {'stacks': sorted(s['StackName'] for s in stacks), 'functions': sorted(functions),
            'clusters': sorted(clusters), 'instances': sorted(instances)}


def name_of(item):
    return item['Name'].lstrip('/')


def fingerprint(item):
    # State/IPs can change after stop. Identity, mounts and labels cannot.
    value = {k: item.get(k) for k in ('Id', 'Created', 'Image')}
    # Mount order is not configuration. Preserve every field but canonicalize the
    # array as well as object keys so equivalent Docker inspect results agree.
    value['Mounts'] = sorted(item.get('Mounts') or [], key=lambda mount: json.dumps(mount, sort_keys=True))
    value['name'] = name_of(item)
    value['labels'] = item['Config'].get('Labels') or {}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def resource_fingerprint(item, kind):
    keys = ('Name', 'CreatedAt', 'Driver', 'Options', 'Labels') if kind == 'volumes' else ('Id', 'Created', 'Name', 'Driver', 'Labels')
    return hashlib.sha256(json.dumps({k: item.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()


def make_plan(data, emulator_name, facts, extra_ids=(), extra_networks=(), extra_volumes=()):
    containers = data['containers']
    matches = [c for c in containers if name_of(c) == emulator_name or c['Id'] == emulator_name]
    if len(matches) != 1: raise ValueError('Select one observed emulator by exact name or full ID')
    emulator = matches[0]
    if not re.match(r'^localstack/localstack(?:-pro)?(?::|@|$)', emulator['Config']['Image']):
        raise ValueError('Selected container is not a LocalStack image')
    others = [c for c in containers if c['Id'] != emulator['Id'] and
              re.match(r'^localstack/localstack(?:-pro)?(?::|@|$)', c['Config']['Image'])]
    if others: raise ValueError('Multiple LocalStack emulators on this engine; shared runtime attribution needs manual review')
    bindings = emulator.get('HostConfig', {}).get('PortBindings') or {}
    if not any(p['HostPort'] == '4566' for p in bindings.get('4566/tcp') or []):
        raise ValueError('Selected emulator does not publish verified local gateway 4566')
    selected = {emulator['Id']: 'Explicitly selected whole LocalStack emulator'}
    groups = {}
    for c in containers:
        cluster = (c['Config'].get('Labels') or {}).get('k3d.cluster')
        if cluster and cluster.startswith(PREFIX): groups.setdefault(cluster, []).append(c)
    owned_clusters = []
    for cluster, nodes in groups.items():
        agents = [c for c in nodes if c['Config']['Labels'].get('k3d.role') == 'agent']
        ids = []
        for agent in agents:
            flags = [a.rsplit('/', 1)[-1] for a in agent['Config'].get('Cmd') or [] if a.startswith('--kubelet-arg=provider-id=')]
            ids.append(flags[-1] if flags else None)
        if agents and all(i in facts['instances'] for i in ids) and all(
                c['Config']['Labels'].get('k3d.role') in ('agent', 'server', 'loadbalancer') or
                (c['Config']['Labels'].get('k3d.role') == 'noRole'
                 and c['Config']['Image'].startswith('ghcr.io/k3d-io/k3d-tools:')) for c in nodes):
            owned_clusters.append(cluster)
            for c in nodes: selected[c['Id']] = 'k3d cluster matched to owned EC2 worker instances: ' + cluster
    for c in containers:
        env = dict(v.split('=', 1) for v in c['Config'].get('Env', []) if '=' in v)
        fn = env.get('AWS_LAMBDA_FUNCTION_NAME')
        prefix = (name_of(emulator) + '-lambda-' + (fn or '').lower() + '-').replace('_', '-')
        if fn in facts['functions'] and name_of(c).startswith(prefix):
            selected[c['Id']] = 'Owned Lambda function and selected emulator runtime name: ' + fn
        # Single-emulator engine; both provider names require an owned EC2 instance.
        if any(name_of(c) in (name_of(emulator) + '-ec2-' + iid, 'localstack-ec2.' + iid)
               for iid in facts['instances']):
            selected[c['Id']] = 'Selected emulator and owned EC2 instance ID'
    observed = {c['Id'] for c in containers}
    for cid in extra_ids:
        if not re.fullmatch(r'[0-9a-f]{64}', cid) or cid not in observed:
            raise ValueError('--include-container requires an observed full 64-character Docker ID')
        selected[cid] = 'Explicit operator selection; ownership must be reviewed before apply'
    planned = [c for c in containers if c['Id'] in selected]
    mounted = {m['Name'] for c in planned for m in c.get('Mounts', []) if m['Type'] == 'volume'}
    if set(extra_volumes) - {v['Name'] for v in data['volumes']}:
        raise ValueError('--include-volume requires an observed exact volume name')
    if set(extra_networks) - {n['Id'] for n in data['networks'] if n['Name'] not in BUILTIN_NETWORKS}:
        raise ValueError('--include-network requires an observed full ID of a non-builtin network')
    retained, volumes, networks = [], [], []
    for volume in data['volumes']:
        if (volume['Name'] not in mounted and volume['Name'] not in extra_volumes
                and (volume.get('Labels') or {}).get('lab.launcher') != 'localstack/lab-v1'): continue
        users = [name_of(c) for c in containers if c['Id'] not in selected and
                 any(m.get('Name') == volume['Name'] and m['Type'] == 'volume' for m in c.get('Mounts', []))]
        entry = {'name': volume['Name'], 'fingerprint': resource_fingerprint(volume, 'volumes')}
        if users: retained.append({'kind': 'volume', 'name': volume['Name'], 'reason': 'Shared with containers', 'users': users})
        else: volumes.append(entry)
    for network in data['networks']:
        if network['Name'] in BUILTIN_NETWORKS: continue
        members = set((network.get('Containers') or {}).keys())
        owned = (network['Id'] in extra_networks or network['Name'] in {'k3d-' + c for c in owned_clusters} or
                 (network['Name'] == 'localstack-ec2-link-local' and bool(members & set(selected))) or
                 (network.get('Labels') or {}).get('lab.launcher') == 'localstack/lab-v1')
        if not owned:
            if members & set(selected):
                retained.append({'kind': 'network', 'name': network['Name'], 'reason': 'Ownership not established; retained'})
            continue
        entry = {'id': network['Id'], 'name': network['Name'], 'fingerprint': resource_fingerprint(network, 'networks')}
        if members - set(selected): retained.append({'kind': 'network', 'name': network['Name'], 'reason': 'Foreign endpoints attached; retained'})
        else: networks.append(entry)
    images = []
    used = {c['Image'] for c in planned}
    for im in data['images']:
        tags = im.get('RepoTags') or []
        # Only lab tags, never shared public base images or arbitrary build cache.
        owned = tags and all(t == 'docs-simulator:local' or
                            re.match(r'^000000000000\.dkr\.ecr\.us-east-1\.localhost\.localstack\.cloud:4566/micro-test-local-docs:', t)
                            for t in tags)
        if owned and not any(c['Id'] not in selected and c['Image'] == im['Id'] for c in containers):
            images.append({'id': im['Id'], 'tags': sorted(tags)})
        elif im['Id'] in used:
            retained.append({'kind': 'image', 'id': im['Id'], 'tags': tags, 'reason': 'Shared/public image cache; not automatically removed'})
    env = dict(v.split('=', 1) for v in emulator['Config'].get('Env', []) if '=' in v)
    candidates = [{'id': c['Id'], 'name': name_of(c), 'image': c['Config']['Image']}
                  for c in containers if c['Id'] not in selected]
    binds = [{'container': name_of(c), 'source': m['Source'], 'destination': m['Destination']}
             for c in planned for m in c.get('Mounts', []) if m['Type'] == 'bind'
             and m['Source'] != '/var/run/docker.sock']
    return {'schema': 2, 'engine': data['engine'], 'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'emulator': emulator['Id'], 'persistenceConfigured': env.get('PERSISTENCE', env.get('LOCALSTACK_PERSISTENCE')) == '1',
            'facts': facts, 'containers': [{'id': c['Id'], 'name': name_of(c), 'image': c['Config']['Image'],
                          'fingerprint': fingerprint(c), 'reason': selected[c['Id']]} for c in planned],
            'volumes': volumes, 'networks': networks, 'images': images, 'retained': retained,
            'unselectedNetworks': [{'id': n['Id'], 'name': n['Name']} for n in data['networks']
                                   if n['Name'] not in BUILTIN_NETWORKS and n['Id'] not in {x['id'] for x in networks}],
            'unselectedVolumes': [v['Name'] for v in data['volumes'] if v['Name'] not in {x['name'] for x in volumes}],
            'unselectedContainers': candidates, 'bindMountsRetained': binds,
            'warning': 'Purge destroys ALL data in selected emulator/volumes, including resources created manually. Plan is not a backup. Unselected containers may include RDS/Karpenter helpers requiring explicit review.'}


def check_plan(plan, data):
    if plan.get('schema') != 2:
        raise ValueError('Cleanup plan uses an old/unsupported fingerprint schema; regenerate with cleanup plan')
    if plan.get('engine') != data['engine']:
        raise ValueError('Cleanup plan belongs to another engine')
    if plan['emulator'] not in {c['id'] for c in plan['containers']}:
        raise ValueError('Plan must explicitly include selected emulator')
    current = {c['Id']: c for c in data['containers']}
    for item in plan['containers']:
        if item['id'] in current and fingerprint(current[item['id']]) != item['fingerprint']:
            raise ValueError('Container identity/configuration changed; rebuild plan: ' + item['name'])
    selected = {c['id'] for c in plan['containers']}
    # A new Lambda or k3d helper cannot silently escape a successful cleanup.
    if plan['emulator'] in current:
        refreshed = make_plan(data, plan['emulator'], plan['facts'])
        if {c['id'] for c in refreshed['containers']} - selected:
            raise ValueError('New lab containers appeared; pause producers and rebuild cleanup plan')
    for kind in ('volumes', 'networks'):
        actual = {x['Name'] if kind == 'volumes' else x['Id']: x for x in data[kind]}
        for item in plan[kind]:
            value = actual.get(item['name'] if kind == 'volumes' else item['id'])
            if value and resource_fingerprint(value, kind) != item['fingerprint']:
                raise ValueError('Resource replaced since inventory: ' + item['name'])
            if kind == 'volumes' and any(c['Id'] not in selected and
                    any(m.get('Name') == item['name'] and m['Type'] == 'volume' for m in c.get('Mounts', [])) for c in current.values()):
                raise ValueError('Volume acquired a foreign user: ' + item['name'])
            if kind == 'networks' and value and set((value.get('Containers') or {}).keys()) - selected:
                raise ValueError('Network acquired foreign endpoints: ' + item['name'])
    return current


def require_idle(lab):
    if not lab.stack(PREFIX + 'business'): return
    body = lab.client('cloudformation').get_template(StackName=PREFIX + 'business')['TemplateBody']
    if isinstance(body, str):
        import yaml
        body = yaml.safe_load(body)
    if 'Schedule' in body['Resources']:
        rule = lab.client('events').describe_rule(Name=PREFIX + 'publisher')
        if rule['State'] != 'DISABLED':
            raise ValueError('Legacy publisher rule active; run ./localstack/lab pause first')
    current = pipe(lab)
    if (current and current['CurrentState'] != 'STOPPED') or publisher_schedule(lab)['State'] != 'DISABLED':
        raise ValueError('Run ./localstack/lab pause first, stop new API requests, then rebuild plan')
    executions = lab.client('stepfunctions').list_executions(
        stateMachineArn=lab.outputs('business')['StateMachineArn'], statusFilter='RUNNING', maxResults=1)['executions']
    if executions: raise ValueError('Wait for active purchase workflows to finish before cleanup')


def apply_plan(plan, mode, delete_data=False, remove_images=False):
    if mode not in ('stop', 'purge'): raise ValueError('Expected stop or purge')
    if mode == 'purge' and not delete_data:
        raise ValueError('purge requires --delete-data: sales, PDFs, SQL and infrastructure state will be lost')
    if mode == 'stop' and not plan['persistenceConfigured'] and not delete_data:
        raise ValueError('Persistence disabled: stopping can lose data. Explicit --delete-data acknowledgment required')
    data = inventory(); current = check_plan(plan, data)
    emulator = current.get(plan['emulator'])
    if emulator and emulator['State']['Running']:
        require_idle(Lab(ENDPOINT))
    # Stop Kubernetes nodes first to halt Karpenter; stop emulator after all other runtimes.
    ordered = sorted(plan['containers'], key=lambda c: (c['id'] == plan['emulator'],
                     not bool((current.get(c['id'], {}).get('Config', {}).get('Labels') or {}).get('k3d.cluster'))))
    for item in ordered:
        # Stopping a parent can auto-remove a helper. Never act on stale Docker IDs.
        current = check_plan(plan, inventory())
        if item['id'] in current and current[item['id']]['State']['Running']:
            print('Stopping ' + item['name'], flush=True)
            run(DOCKER + ['stop', '--time', '120', item['id']])
    after_stop = inventory(); check_plan(plan, after_stop)
    remaining = {c['Id']: c for c in after_stop['containers']}
    if any(remaining[c['id']]['State']['Running'] for c in ordered if c['id'] in remaining):
        raise RuntimeError('A selected container restarted; cleanup incomplete')
    if mode == 'purge':
        for item in ordered:
            current = check_plan(plan, inventory())
            if item['id'] in current:
                print('Removing ' + item['name'], flush=True)
                run(DOCKER + ['container', 'rm', item['id']])  # No -f/-v; volumes explicit below.
        for kind, verb in (('networks', 'network'), ('volumes', 'volume')):
            now = inventory(); check_plan(plan, now)
            present = {x['Id'] if kind == 'networks' else x['Name'] for x in now[kind]}
            for item in plan[kind]:
                identifier = item['id'] if kind == 'networks' else item['name']
                if identifier in present: run(DOCKER + [verb, 'rm', identifier])
        if remove_images:
            now = inventory()
            for item in plan['images']:
                actual = next((i for i in now['images'] if i['Id'] == item['id']), None)
                if not actual: continue
                if sorted(actual.get('RepoTags') or []) != item['tags'] or any(c['Image'] == item['id'] for c in now['containers']):
                    raise ValueError('Image tags/usage changed; retained: ' + item['id'])
                for tag in item['tags']: run(DOCKER + ['image', 'rm', '--no-prune', tag])
    final = inventory()
    ids = {c['Id'] for c in final['containers']}
    residues = [c['name'] for c in plan['containers'] if c['id'] in ids and
                (mode == 'purge' or next(x for x in final['containers'] if x['Id'] == c['id'])['State']['Running'])]
    if mode == 'purge':
        residues += [v['name'] for v in plan['volumes'] if v['name'] in {x['Name'] for x in final['volumes']}]
        residues += [n['name'] for n in plan['networks'] if n['id'] in {x['Id'] for x in final['networks']}]
    return {'status': 'INCOMPLETE' if residues else 'SELECTED_SCOPE_COMPLETE', 'mode': mode, 'residues': residues,
            'retained': plan['retained'], 'bindMountsRetained': plan['bindMountsRetained'],
            'remainingContainers': [{'id': c['Id'], 'name': name_of(c), 'image': c['Config']['Image']} for c in final['containers']],
            'note': 'Not a zero-disk claim: review remaining helpers, shared caches, bind mounts and local files. Docker disk image compaction is separate.'}


def files_ready(plan, data):
    """Local receipts are not sufficient: verify the reviewed Docker scope is gone."""
    current = check_plan(plan, data)
    if any(c['id'] in current for c in plan['containers']):
        raise ValueError('Purge selected containers before deleting local state')
    if any(v['name'] in {x['Name'] for x in data['volumes']} for v in plan['volumes']):
        raise ValueError('Purge selected volumes before deleting local state')
    if any(n['id'] in {x['Id'] for x in data['networks']} for n in plan['networks']):
        raise ValueError('Purge selected networks before deleting local state')
    for c in data['containers']:
        if re.match(r'^localstack/localstack(?:-pro)?(?::|@|$)', c['Config']['Image']):
            raise ValueError('An emulator exists; do not erase local state after recreating the lab')
        for mount in c.get('Mounts', []):
            if mount['Type'] == 'bind':
                source = Path(mount['Source']).resolve()
                if any(source.is_relative_to(ROOT / name) or (ROOT / name).is_relative_to(source) for name in FILE_ROOTS):
                    raise ValueError('Remaining container uses generated files: ' + name_of(c))


def clean_files(apply=False, roots=FILE_ROOTS):
    """Only generated paths in this checkout. Never follow a symlink or remove Git sources."""
    if not set(roots).issubset(FILE_ROOTS):
        raise ValueError('Only generated runtime directories may be removed')
    paths = [ROOT / name for name in roots]
    for parent, dirs, _ in os.walk(ROOT, followlinks=False):
        if '__pycache__' in dirs: paths.append(Path(parent) / '__pycache__')
        dirs[:] = [d for d in dirs if d not in (*FILE_ROOTS, '__pycache__', '.git')]
    tracked = subprocess.check_output(['git', '-C', str(ROOT.parent), 'ls-files', '-z']).decode().split('\0')
    for path in paths:
        if path.is_symlink(): raise ValueError('Generated root is a symlink; inspect manually: ' + str(path))
        relative = path.relative_to(ROOT.parent).as_posix()
        if any(p == relative or p.startswith(relative + '/') for p in tracked):
            raise ValueError('Generated path contains tracked sources: ' + relative)
    observer = ROOT / '.state/observability/process.json'
    if observer.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location('cleanup_observer', ROOT / 'observability/observe.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        if module.own_process(json.loads(observer.read_text())['pid']):
            raise ValueError('Stop this checkout observer first; do not delete state of a live process')
    result = {'status': 'PLANNED', 'paths': [str(p) for p in paths if p.exists()],
              'warning': 'Deletes generated tokens, kubeconfig, test reports, local snapshots, downloaded tools and venv. Does not back up data.'}
    if apply:
        for path in paths:
            if path.is_dir(): shutil.rmtree(path)
        result['status'] = 'REMOVED'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['plan', 'stop', 'purge', 'files'])
    parser.add_argument('--emulator', help='Explicit observed emulator name, e.g. localstack-aws')
    parser.add_argument('--plan', type=Path, help='Reviewed inventory JSON outside generated directories')
    parser.add_argument('--include-container', action='append', default=[], help='Explicit full Docker ID for an unclassified helper; inspect first')
    parser.add_argument('--include-network', action='append', default=[], help='Reviewed orphan network full ID; never a builtin network')
    parser.add_argument('--include-volume', action='append', default=[], help='Reviewed orphan volume exact name')
    parser.add_argument('--apply', action='store_true', help='Perform reviewed actions; otherwise no changes')
    parser.add_argument('--delete-data', action='store_true')
    parser.add_argument('--images', action='store_true', help='purge only: also remove reviewed, unshared lab image tags')
    args = parser.parse_args(argv)
    if not args.plan: parser.error('--plan required (e.g. $HOME/lab-cleanup.json)')
    if args.images and args.mode != 'purge': parser.error('--images is only valid with purge')
    require_wsl()
    if args.mode == 'plan':
        if args.apply or args.delete_data: parser.error('plan is read-only; omit --apply/--delete-data')
        if not args.emulator: parser.error('plan requires --emulator from docker ps -a')
        data = inventory()
        chosen = [c for c in data['containers'] if name_of(c) == args.emulator or c['Id'] == args.emulator]
        if len(chosen) != 1 or not chosen[0]['State']['Running']:
            raise ValueError('Plan requires the selected emulator running for ownership discovery; reuse saved plan after stop')
        facts = cloud_facts(Lab(ENDPOINT))
        result = make_plan(data, args.emulator, facts, args.include_container, args.include_network, args.include_volume)
        write(args.plan, result)
        print(json.dumps(result, indent=2)); print('Plan: ' + str(args.plan)); return
    if args.include_container or args.include_network or args.include_volume or args.emulator:
        parser.error('Selection changes require a new plan')
    args.plan = report_path(args.plan)
    receipt = report_path(args.plan.with_suffix('.result.json'))
    plan = json.loads(args.plan.read_text())
    if args.mode == 'files':
        if args.apply and not args.delete_data: parser.error('files --apply requires --delete-data')
        files_ready(plan, inventory())
        print(json.dumps(clean_files(args.apply), indent=2)); return
    if not args.apply:
        check_plan(plan, inventory())
        print(json.dumps({'status': 'DRY_RUN', 'mode': args.mode, 'plan': plan}, indent=2)); return
    try:
        result = apply_plan(plan, args.mode, args.delete_data, args.images)
    except Exception as exc:
        # Report failure without claiming rollback or successful cleanup.
        write(receipt, {'status': 'INCOMPLETE', 'mode': args.mode, 'error': str(exc)[:1000]})
        raise
    write(receipt, result)
    print(json.dumps(result, indent=2)); print('Result: ' + str(receipt))
    if result['status'] == 'INCOMPLETE': raise SystemExit(1)


if __name__ == '__main__': main()
