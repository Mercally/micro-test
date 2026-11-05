#!/usr/bin/env python3
"""WSL-only scaling experiments on the existing, verified laptop EKS cluster."""
import argparse
import importlib.util
import ipaddress
import json
import os
import re
from decimal import Decimal
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'business'))
from business import Lab, save, report_dir
from wsl import required_tools, runtime_binding, select_cluster
from kube import Kube, DOCKER, run, nodeport_target

NS = 'micro-test-local-general'
MICRO = 'micro-test-local-micro'


def discover(network):
    if sys.platform != 'linux' or 'microsoft' not in Path('/proc/sys/kernel/osrelease').read_text().lower():
        raise ValueError('Run inside laptop WSL2; no remote Docker/Kubernetes context accepted')
    required_tools(True)
    os.environ.pop('DOCKER_CONTEXT', None)
    os.environ['DOCKER_HOST'] = 'unix:///var/run/docker.sock'
    lab = Lab('http://127.0.0.1:4566')
    ids = run(DOCKER + ['ps', '-q', '--no-trunc']).stdout.split()
    if not ids:
        raise ValueError('No Docker containers')
    containers = json.loads(run(DOCKER + ['inspect'] + ids).stdout)
    local = [c for c in containers if c['Config']['Image'].startswith('localstack/localstack')
             and any(p['HostPort'] == '4566' for p in c['NetworkSettings']['Ports'].get('4566/tcp') or [])]
    if len(local) != 1 or network not in local[0]['NetworkSettings']['Networks']:
        raise ValueError('Expected one LocalStack gateway attached to selected network')
    clusters = json.loads(run(['k3d', 'cluster', 'list', '-o', 'json']).stdout)
    name = select_cluster(clusters, network)
    cluster = next(c for c in clusters if c['name'] == name)
    eks_name = lab.outputs('eks')['ClusterName']
    binding = runtime_binding(lab, eks_name, cluster, network, containers)
    kube = Kube(name, expected_network=network, expected_api_port=binding['apiPort'])
    return lab, kube, local[0], eks_name


def inspect(kube):
    result = {'status': 'INSPECTED', 'cluster': kube.inspect()}
    for key, args in {
        'hpa': ['-n', NS, 'get', 'hpa', '-o', 'json'],
        'metrics': ['get', '--raw', '/apis/metrics.k8s.io/v1beta1/namespaces/' + NS + '/pods'],
        'pods': ['-n', NS, 'get', 'pods', '-o', 'wide'],
        'nodeClaims': ['get', 'nodeclaims', '-o', 'wide'],
    }.items():
        response = kube.call(*args, check=False)
        result[key] = {'exitCode': response.returncode, 'output': response.stdout or response.stderr}
    return result


def memory_bytes(value):
    """Parse byte quantities used in this lab; reject unsupported units explicitly."""
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|K|M|G)?', str(value))
    if not match:
        raise ValueError('Use memory quantities in bytes, Ki/Mi/Gi or K/M/G: ' + str(value))
    units = {'': 1, 'Ki': 1024, 'Mi': 1024**2, 'Gi': 1024**3, 'K': 1000, 'M': 1000**2, 'G': 1000**3}
    return Decimal(match[1]) * units[match[2] or '']


def hpa_manifest(resources, memory_target_mib=512):
    import yaml
    requests, limits = resources.get('requests', {}), resources.get('limits', {})
    if not requests.get('cpu') or not requests.get('memory') or not limits.get('memory'):
        raise ValueError('Micro CPU/memory requests and memory limit required before HPA installation')
    limit = memory_bytes(limits['memory'])
    if memory_bytes(requests['memory']) <= 0 or memory_bytes(requests['memory']) > limit:
        raise ValueError('Memory request must be positive and not exceed limit')
    if memory_target_mib <= 0 or memory_target_mib * 1024**2 >= limit:
        raise ValueError('HPA memory target must be positive and below the micro memory limit')
    manifest = yaml.safe_load((HERE / 'hpa.yaml').read_text())
    for metric in manifest['spec']['metrics']:
        if metric['containerResource']['name'] == 'memory':
            metric['containerResource']['target']['averageValue'] = f'{memory_target_mib}Mi'
    return manifest


def install_hpa(kube, memory_target_mib=512):
    deployment = json.loads(kube.call('-n', NS, 'get', 'deployment', MICRO, '-o', 'json').stdout)
    micro = next(c for c in deployment['spec']['template']['spec']['containers'] if c['name'] == 'micro')
    resources = micro.get('resources', {})
    manifest = hpa_manifest(resources, memory_target_mib)
    metrics = json.loads(kube.call('get', '--raw', '/apis/metrics.k8s.io/v1beta1/namespaces/' + NS + '/pods').stdout)
    samples = [{'pod': p['metadata']['name'], 'usage': c.get('usage', {})}
               for p in metrics.get('items', []) if p['metadata']['name'].startswith(MICRO + '-')
               for c in p['containers'] if c['name'] == 'micro']
    if not samples or any(not all(s['usage'].get(key) for key in ('cpu', 'memory')) for s in samples):
        raise ValueError('metrics-server must provide CPU AND memory samples for micro before installing HPA')
    existing = kube.call('-n', NS, 'get', 'hpa', MICRO, '--ignore-not-found', '-o', 'json').stdout
    if existing and json.loads(existing)['metadata'].get('labels', {}).get('lab') != 'micro-test-local':
        raise ValueError('Existing HPA not owned by scaling lab')
    kube.apply_data(manifest)
    return {'status': 'APPLIED', 'resources': resources, 'observedSamples': samples,
            'memoryTargetMiB': memory_target_mib,
            'hpa': json.loads(kube.call('-n', NS, 'get', 'hpa', MICRO, '-o', 'json').stdout),
            'note': 'CPU/memory recommendations use maximum, not AND. Calibrate memory against warmed-up baseline; load and scale-down tests still required.'}


def sync_targets(lab, kube, network, mode):
    # Do not silently replace pod targeting with a Docker/NodePort approximation.
    spec = importlib.util.spec_from_file_location('scaling_flow', HERE / 'flow.py')
    flow = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(flow)
    out = flow.target_group(lab)
    if mode == 'pods':
        pods = json.loads(kube.call('-n', NS, 'get', 'pods', '-l', 'app=micro-test-local-gateway', '-o', 'json').stdout)['items']
        targets = [{'Id': p['status']['podIP'], 'Port': 8080, 'AvailabilityZone': 'all'} for p in pods
                   if not p['metadata'].get('deletionTimestamp') and
                   any(c['type'] == 'Ready' and c['status'] == 'True' for c in p.get('status', {}).get('conditions', []))]
    else:
        node = nodeport_target(kube, network)
        targets = [{'Id': node['ip'], 'Port': 31080, 'AvailabilityZone': 'all'}]
    if not targets or any(not ipaddress.ip_address(t['Id']).is_private for t in targets):
        raise ValueError('No observed private Ready gateway targets')
    elb = lab.client('elbv2')
    old = [x['Target'] for x in elb.describe_target_health(TargetGroupArn=out['TargetGroupArn'])['TargetHealthDescriptions']]
    if {(t['Id'], t['Port']) for t in old} != {(t['Id'], t['Port']) for t in targets}:
        from http_workflow import live_http
        from business import pipe
        if lab.stack('micro-test-local-business') and live_http(lab):
            running = lab.client('stepfunctions').list_executions(
                stateMachineArn=lab.outputs('business')['StateMachineArn'], statusFilter='RUNNING', maxResults=1)['executions']
            if pipe(lab)['CurrentState'] != 'STOPPED' or running:
                raise ValueError('NLB now serves purchases: pause Pipe and finish active workflows before changing targets')
    save(kube.report_dir / 'targets-before.json', {'targets': old, 'desired': targets, 'mode': mode})
    remove = [t for t in old if (t['Id'], t['Port']) not in {(n['Id'], n['Port']) for n in targets}]
    try:
        elb.register_targets(TargetGroupArn=out['TargetGroupArn'], Targets=targets)
        if remove:
            elb.deregister_targets(TargetGroupArn=out['TargetGroupArn'], Targets=remove)
        result = flow.probe(lab)
        if result['status'] != 'PASS':
            raise RuntimeError('Target experiment failed native HTTP health')
    except Exception:
        # Restore prior targets after a failed experiment; never leave business
        # traffic on an unverified alternate pod/node address.
        if old:
            elb.register_targets(TargetGroupArn=out['TargetGroupArn'], Targets=old)
        added = [t for t in targets if (t['Id'], t['Port']) not in {(n['Id'], n['Port']) for n in old}]
        if added:
            elb.deregister_targets(TargetGroupArn=out['TargetGroupArn'], Targets=added)
        save(kube.report_dir / 'targets-rollback.json', {'restoredTargets': old, 'health': flow.probe(lab)})
        raise
    result['targetMode'] = mode
    result['reconciliation'] = 'One-shot; rerun after gateway pod or node changes. No load balancer controller installed.'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'hpa', 'nlb', 'karpenter', 'node-test'])
    parser.add_argument('--network', required=True)
    parser.add_argument('--targets', choices=['pods', 'nodeport'], default='nodeport')
    parser.add_argument('--memory-target-mib', type=int, default=512,
                        help='HPA only: initial mean micro memory target; calibrate with measured baseline')
    args = parser.parse_args()
    path = report_dir() / ('scaling-' + args.command + '.json')
    try:
        lab, kube, local, eks_name = discover(args.network)
        if args.command == 'inspect':
            result = inspect(kube)
        elif args.command == 'hpa':
            result = install_hpa(kube, args.memory_target_mib)
        elif args.command == 'nlb':
            result = sync_targets(lab, kube, args.network, args.targets)
        else:
            spec = importlib.util.spec_from_file_location('scaling_karpenter', HERE / 'karpenter.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result = (module.install(lab, kube, local, eks_name, args.network) if args.command == 'karpenter'
                      else module.node_test(lab, kube))
        save(path, result)
        print(json.dumps(result, default=str, indent=2))
        if result.get('status') == 'FAIL':
            raise SystemExit(1)
    except Exception as exc:
        save(path, {'status': 'FAIL', 'error': str(exc)[:2000]})
        raise
    finally:
        print('Report: ' + str(path), flush=True)


if __name__ == '__main__':
    main()
