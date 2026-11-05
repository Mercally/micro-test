"""Experimental AWS Karpenter on LocalStack; real workers, isolated practice pool."""
import hashlib
import ipaddress
import json
from pathlib import Path
import shutil
import tarfile
import time
import yaml

from business import save
from kube import run

NS = 'micro-test-local-karpenter'
POOL = 'micro-test-local-practice'
VERSION = '1.8.2'
CHART_SHA = '381a6a73c23f48af9890ab31bd4d8bacbcd2d3271be98e4cd5aab5cebb3e6305'
LABEL = {'lab': 'micro-test-local'}
RESTRICTED = {'local-aws-only': 'true'}


def helm_binary():
    current = shutil.which('helm')
    if current:
        return current
    if not shutil.which('curl'):
        raise ValueError('curl required to download verified local Helm binary')
    directory = Path(__file__).resolve().parents[1] / '.state/tools/helm-3.19.0'
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / 'helm.tar.gz'
    run(['curl', '--fail', '--silent', '--show-error', '--location', '--proto', '=https',
         'https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz', '-o', str(archive)])
    if hashlib.sha256(archive.read_bytes()).hexdigest() != 'a7f81ce08007091b86d8bd696eb4d86b8d0f2e1b9f6c714be62f82f96a594496':
        raise ValueError('Helm download checksum mismatch')
    binary = directory / 'helm'
    with tarfile.open(archive) as bundle:
        binary.write_bytes(bundle.extractfile('linux-amd64/helm').read())
    binary.chmod(0o700)
    return str(binary)


def values(address, cluster):
    ip = ipaddress.ip_address(address)
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_unspecified:
        raise ValueError('Observed private Docker IPv4 required')
    if cluster != 'micro-test-local-eks':
        raise ValueError('Only dedicated lab EKS cluster supported')
    endpoint = f'http://{address}:4566'
    env = {'AWS_ACCESS_KEY_ID': 'test', 'AWS_SECRET_ACCESS_KEY': 'test',
           'AWS_REGION': 'us-east-1', 'AWS_DEFAULT_REGION': 'us-east-1',
           'AWS_EC2_METADATA_DISABLED': 'true', 'AWS_ENDPOINT_URL': endpoint,
           'AWS_IGNORE_CONFIGURED_ENDPOINT_URLS': 'false'}
    for service in ('EC2', 'EKS', 'IAM', 'SSM', 'STS', 'SQS', 'PRICING'):
        env['AWS_ENDPOINT_URL_' + service] = endpoint
    return {'replicas': 1, 'podLabels': {**LABEL, **RESTRICTED},
            'nodeSelector': {'kubernetes.io/os': 'linux', 'lab-role': 'general'},
            'topologySpreadConstraints': [],
            'settings': {'clusterName': cluster, 'isolatedVPC': True, 'eksControlPlane': True,
                         'featureGates': {'reservedCapacity': False}},
            'controller': {'env': [{'name': k, 'value': v} for k, v in env.items()],
                           'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'},
                                         'limits': {'cpu': '1', 'memory': '512Mi'}}}}


def node_resources(net, profile, version):
    node_class = {'apiVersion': 'karpenter.k8s.aws/v1', 'kind': 'EC2NodeClass',
                  'metadata': {'name': POOL, 'labels': LABEL},
                  'spec': {'amiFamily': 'AL2023', 'instanceProfile': profile,
                           'amiSelectorTerms': [{'ssmParameter': f'/aws/service/eks/optimized-ami/{version}/amazon-linux-2023/x86_64/standard/recommended/image_id'}],
                           'subnetSelectorTerms': [{'id': net['GeneralA']}],
                           'securityGroupSelectorTerms': [{'id': net['ClusterSG']}],
                           'tags': {'Lab': 'micro-test-local'}, 'kubelet': {'maxPods': 20}}}
    pool = {'apiVersion': 'karpenter.sh/v1', 'kind': 'NodePool',
            'metadata': {'name': POOL, 'labels': LABEL},
            'spec': {'limits': {'cpu': '2'},
                     'disruption': {'consolidationPolicy': 'WhenEmpty', 'consolidateAfter': 'Never',
                                    'budgets': [{'nodes': '0'}]},
                     'template': {'metadata': {'labels': {**LABEL, 'lab-role': 'karpenter-practice'}},
                                  'spec': {'nodeClassRef': {'group': 'karpenter.k8s.aws', 'kind': 'EC2NodeClass', 'name': POOL},
                                           'expireAfter': 'Never',
                                           'taints': [{'key': 'dedicated', 'value': 'karpenter-practice', 'effect': 'NoSchedule'}],
                                           'requirements': [
                                               {'key': 'kubernetes.io/arch', 'operator': 'In', 'values': ['amd64']},
                                               {'key': 'kubernetes.io/os', 'operator': 'In', 'values': ['linux']},
                                               {'key': 'karpenter.sh/capacity-type', 'operator': 'In', 'values': ['on-demand']},
                                               {'key': 'node.kubernetes.io/instance-type', 'operator': 'In', 'values': ['t3.medium']} ]}}}}
    return [node_class, pool]


def egress_policy(address, api):
    rules = [{'to': [{'ipBlock': {'cidr': address + '/32'}}], 'ports': [{'protocol': 'TCP', 'port': 4566}]},
             {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                      'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
              'ports': [{'protocol': p, 'port': 53} for p in ('UDP', 'TCP')]}]
    for subset in api['subsets']:
        rules.append({'to': [{'ipBlock': {'cidr': a['ip'] + '/32'}} for a in subset['addresses']],
                      'ports': [{'protocol': 'TCP', 'port': p['port']} for p in subset['ports']]})
    return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
            'metadata': {'name': 'local-aws-only', 'namespace': NS, 'labels': LABEL},
            'spec': {'podSelector': {'matchLabels': RESTRICTED}, 'policyTypes': ['Egress'], 'egress': rules}}


def network_gate(kube, address, denied_port):
    name = 'egress-proof'
    exists = kube.call('-n', NS, 'get', 'pod', name, '--ignore-not-found', '-o', 'name').stdout
    if exists:
        raise ValueError('Prior egress-proof pod exists; inspect it before deleting/retrying')
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': NS, 'labels': LABEL},
           'spec': {'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                    'nodeSelector': {'lab-role': 'general'},
                    'containers': [{'name': 'probe', 'image': 'python:3.12.12-alpine3.22',
                                    'command': ['python', '-c', 'import time; time.sleep(600)'],
                                    'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                                                  'limits': {'cpu': '100m', 'memory': '64Mi'}}}]}}
    kube.apply_data(pod)
    try:
        kube.call('-n', NS, 'wait', '--for=condition=Ready', 'pod/' + name, '--timeout=180s')
        code = 'import socket; socket.create_connection((' + repr(address) + ', ' + str(denied_port) + '), timeout=3).close()'
        kube.call('-n', NS, 'exec', name, '--', 'python', '-c', code)  # Baseline must succeed.
        kube.call('-n', NS, 'label', 'pod', name, 'local-aws-only=true')
        blocked_code = ('import socket,sys\ntry:\n socket.create_connection(('
                        + repr(address) + ', ' + str(denied_port) + '), timeout=3).close()\n'
                        'except TimeoutError:\n sys.exit(42)\n')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            rejected = kube.call('-n', NS, 'exec', name, '--', 'python', '-c', blocked_code, check=False)
            if rejected.returncode == 42:
                # Confirm the pod still executes and LocalStack's allowed API remains reachable.
                allowed = 'import urllib.request,json; r=json.load(urllib.request.urlopen(' + repr(f'http://{address}:4566/_localstack/health') + ',timeout=5)); assert "services" in r; print("PASS")'
                kube.call('-n', NS, 'exec', name, '--', 'python', '-c', allowed)
                return {'status': 'PASS', 'allowedPort': 4566, 'blockedPort': denied_port,
                        'scope': 'CNI enforcement checked against a previously reachable local port'}
            time.sleep(2)
        raise RuntimeError('NetworkPolicy not enforced; refusing to start Karpenter')
    finally:
        kube.call('-n', NS, 'delete', 'pod', name, '--wait=false', check=False)


def instance_profile(lab, eks_name):
    eks, iam = lab.client('eks'), lab.client('iam')
    groups = eks.list_nodegroups(clusterName=eks_name)['nodegroups']
    roles = {eks.describe_nodegroup(clusterName=eks_name, nodegroupName=g)['nodegroup']['nodeRole'] for g in groups}
    if len(roles) != 1:
        raise ValueError('Expected one existing managed-worker IAM role')
    role = roles.pop().rsplit('/', 1)[-1]
    name = 'micro-test-local-karpenter-profile'
    try:
        value = iam.get_instance_profile(InstanceProfileName=name)['InstanceProfile']
        if {t['Key']: t['Value'] for t in value.get('Tags', [])}.get('Lab') != 'micro-test-local':
            raise ValueError('Existing instance profile is not owned by lab')
    except iam.exceptions.NoSuchEntityException:
        value = iam.create_instance_profile(InstanceProfileName=name, Tags=[{'Key': 'Lab', 'Value': 'micro-test-local'}])['InstanceProfile']
    names = [r['RoleName'] for r in value.get('Roles', [])]
    if names and names != [role]:
        raise ValueError('Unexpected role in instance profile')
    if not names:
        iam.add_role_to_instance_profile(InstanceProfileName=name, RoleName=role)
    return name


def install(lab, kube, local, eks_name, network):
    helm = helm_binary()
    existing = kube.call('get', 'ns', NS, '--ignore-not-found', '-o', 'json').stdout
    if existing and json.loads(existing)['metadata'].get('labels', {}).get('lab') != 'micro-test-local':
        raise ValueError('Existing Karpenter namespace not lab-owned')
    for resource in ('deployments', 'pods'):
        others = json.loads(kube.call('get', resource, '-A', '-l', 'app.kubernetes.io/name=karpenter', '-o', 'json').stdout)['items']
        if any(x['metadata']['namespace'] != NS for x in others):
            raise ValueError('Another Karpenter controller exists; do not run competing controllers')
    crd = kube.call('get', 'crd', 'nodepools.karpenter.sh', '--ignore-not-found', '-o', 'name').stdout
    if crd:
        pools = json.loads(kube.call('get', 'nodepools', '-o', 'json').stdout)['items']
        if any(p['metadata']['name'] != POOL or p['metadata'].get('labels', {}).get('lab') != 'micro-test-local' for p in pools):
            raise ValueError('Other NodePools exist; refusing to adopt them with this controller')
    address = local['NetworkSettings']['Networks'][network]['IPAddress']
    config = values(address, eks_name)
    cluster = lab.client('eks').describe_cluster(name=eks_name)['cluster']
    if cluster['version'] != '1.34':
        raise ValueError('This pinned experiment was prepared for observed Kubernetes 1.34')
    # Require live local dependencies before changing Kubernetes.
    lab.client('ssm').get_parameter(Name=f'/aws/service/eks/optimized-ami/{cluster["version"]}/amazon-linux-2023/x86_64/standard/recommended/image_id')
    dbs = [x for x in lab.client('rds').describe_db_instances()['DBInstances'] if x['DBInstanceIdentifier'].startswith('micro-test-local-')]
    if len(dbs) != 1:
        raise ValueError('Expected one lab RDS endpoint for negative network test')
    directory = Path(__file__).resolve().parents[1] / '.state/scaling'
    directory.mkdir(parents=True, exist_ok=True)
    chart = directory / f'karpenter-{VERSION}.tgz'
    if not chart.exists():
        run([helm, 'pull', 'oci://public.ecr.aws/karpenter/karpenter', '--version', VERSION, '--destination', str(directory)])
    if hashlib.sha256(chart.read_bytes()).hexdigest() != CHART_SHA:
        raise ValueError('Karpenter chart checksum mismatch')
    config_file = directory / 'values.yaml'
    config_file.write_text(yaml.safe_dump(config))
    rendered = run([helm, 'template', 'karpenter', str(chart), '-n', NS, '-f', str(config_file), '--include-crds']).stdout
    (directory / 'karpenter-rendered.yaml').write_text(rendered)
    kube.apply_data({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NS, 'labels': {**LABEL, 'istio-injection': 'disabled'}}})
    api = json.loads(kube.call('-n', 'default', 'get', 'endpoints', 'kubernetes', '-o', 'json').stdout)
    kube.apply_data(egress_policy(address, api))
    print('Checking enforced local-only controller egress before installation', flush=True)
    gate = network_gate(kube, address, dbs[0]['Endpoint']['Port'])
    save(kube.report_dir / 'karpenter-egress.json', gate)
    for doc in yaml.safe_load_all(rendered):
        if doc and doc['kind'] == 'CustomResourceDefinition':
            kube.call('apply', '--server-side', '-f', '-', input=yaml.safe_dump(doc))
    print('Installing pinned Karpenter controller; readiness timeout 180 seconds', flush=True)
    run([helm, '--kubeconfig', str(kube.path), 'upgrade', '--install', 'karpenter', str(chart), '-n', NS,
         '-f', str(config_file), '--wait', '--timeout', '180s'])
    for resource in node_resources(lab.outputs('network'), instance_profile(lab, eks_name), cluster['version']):
        previous = kube.call('get', resource['kind'], POOL, '--ignore-not-found', '-o', 'json').stdout
        if previous and json.loads(previous)['metadata'].get('labels', {}).get('lab') != 'micro-test-local':
            raise ValueError('Existing NodePool/NodeClass not owned')
        kube.apply_data(resource)
    return {'status': 'INSTALLED', 'version': VERSION, 'egress': gate,
            'nodeProvisioning': 'PENDING: run node-test; controller readiness alone is insufficient',
            'scope': 'Same EKS cluster, isolated tainted practice pool. Micro stays on managed general nodes.'}


def node_test(lab, kube):
    deployment = {'apiVersion': 'apps/v1', 'kind': 'Deployment',
                  'metadata': {'name': POOL, 'namespace': NS, 'labels': LABEL},
                  'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': POOL}},
                           'template': {'metadata': {'labels': {'app': POOL}},
                                        'spec': {'automountServiceAccountToken': False,
                                                 'nodeSelector': {'karpenter.sh/nodepool': POOL},
                                                 'tolerations': [{'key': 'dedicated', 'value': 'karpenter-practice', 'effect': 'NoSchedule'}],
                                                 'containers': [{'name': 'http', 'image': 'nginx:1.28.0-alpine',
                                                                 'ports': [{'containerPort': 80}],
                                                                 'readinessProbe': {'httpGet': {'path': '/', 'port': 80}},
                                                                 'resources': {'requests': {'cpu': '100m', 'memory': '64Mi'},
                                                                               'limits': {'cpu': '200m', 'memory': '128Mi'}}}]}}}}
    kube.apply_data(deployment)
    print('Waiting for real Karpenter worker and HTTP pod; timeout 300 seconds', flush=True)
    rollout = kube.call('-n', NS, 'rollout', 'status', 'deployment/' + POOL, '--timeout=300s', check=False)
    pods = json.loads(kube.call('-n', NS, 'get', 'pods', '-l', 'app=' + POOL, '-o', 'json').stdout)['items']
    claims = json.loads(kube.call('get', 'nodeclaims', '-l', 'karpenter.sh/nodepool=' + POOL, '-o', 'json').stdout)['items']
    proof = {'status': 'FAIL', 'rollout': rollout.stdout + rollout.stderr, 'nodeClaims': claims, 'pods': pods}
    if rollout.returncode:
        proof['controllerLogs'] = kube.call('-n', NS, 'logs', 'deployment/karpenter', '--tail=100', check=False).stdout
        return proof
    pod = next(p for p in pods if not p['metadata'].get('deletionTimestamp') and p['status']['phase'] == 'Running')
    node = json.loads(kube.call('get', 'node', pod['spec']['nodeName'], '-o', 'json').stdout)
    provider = node['spec']['providerID']
    matching = [c for c in claims if c.get('status', {}).get('providerID') == provider
                and any(x['type'] == 'Ready' and x['status'] == 'True' for x in c.get('status', {}).get('conditions', []))]
    if not matching:
        proof['error'] = 'Pod runs but matching NodeClaim is not Ready'
        return proof
    instance = lab.client('ec2').describe_instances(InstanceIds=[provider.rsplit('/', 1)[-1]])['Reservations'][0]['Instances'][0]
    if {t['Key']: t['Value'] for t in instance.get('Tags', [])}.get('Lab') != 'micro-test-local':
        raise ValueError('Node instance is not lab-owned')
    html = kube.call('-n', NS, 'exec', pod['metadata']['name'], '--', 'wget', '-qO-', 'http://127.0.0.1/').stdout
    proof.update(status='PASS' if 'Welcome to nginx!' in html else 'FAIL', node=node['metadata']['name'],
                 providerID=provider, httpResponded='Welcome to nginx!' in html,
                 scope='Real HTTP container on AWS-provider Karpenter worker in existing local EKS; not production EC2/IAM parity')
    return proof
