#!/usr/bin/env python3
"""Validacion local reproducible: YAML/JSON, contratos CFN, rutas, CIDR y controles de acceso."""
import ipaddress
import json
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]


class Loader(yaml.SafeLoader):
    pass


def mapping(loader, node):
    pairs = loader.construct_pairs(node, deep=True)
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Clave YAML duplicada: {key}')
        result[key] = value
    return result


def intrinsic(loader, tag, node):
    value = (loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode)
             else loader.construct_sequence(node, deep=True))
    if tag == 'GetAtt' and isinstance(value, str):
        value = value.split('.', 1)
    return {tag if tag == 'Ref' else 'Fn::' + tag: value}


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
Loader.add_multi_constructor('!', intrinsic)


def walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def refs(value):
    for item in walk(value):
        if not isinstance(item, dict):
            continue
        if 'Ref' in item:
            yield item['Ref']
        if 'Fn::GetAtt' in item:
            yield item['Fn::GetAtt'][0]
        if 'Fn::Sub' in item:
            value = item['Fn::Sub']
            text, variables = (value, {}) if isinstance(value, str) else value
            for name in re.findall(r'\$\{([^}]+)\}', text):
                if not name.startswith('!') and name not in variables:
                    yield name.split('.')[0]


def acyclic(graph):
    visiting, done = set(), set()
    def visit(node):
        assert node not in visiting, f'Dependencia circular: {node}'
        if node in done:
            return
        visiting.add(node)
        for dep in graph[node]:
            visit(dep)
        visiting.remove(node)
        done.add(node)
    for node in graph:
        visit(node)


def resolve(value, parameters):
    if isinstance(value, str):
        return value
    return re.sub(r'\$\{([^}]+)\}', lambda m: parameters[m[1]], value['Fn::Sub'])


def resources_of(template, kind):
    return {k: v['Properties'] for k, v in template['Resources'].items() if v['Type'] == 'AWS::' + kind}


def check_architecture(templates, manifests):
    net, eks, services = templates
    n, e, s = [t['Resources'] for t in templates]
    expected = {f'{role}Subnet{z}': f'10.20.{num}.0/24'
                for role, nums in [('General', [11, 12]), ('Terceros', [13, 14]), ('Onprem', [15, 16]), ('Db', [21, 22])]
                for z, num in zip('AB', nums)}
    subnets = resources_of(net, 'EC2::Subnet')
    assert subnets.keys() == expected.keys()
    assert n['VPC']['Properties']['CidrBlock'] == '10.20.0.0/16'
    blocks = []
    for key, value in subnets.items():
        assert value['CidrBlock'] == expected[key]
        assert value['AvailabilityZone'] == 'us-east-1' + key[-1].lower()
        assert value['MapPublicIpOnLaunch'] is False
        block = ipaddress.ip_network(value['CidrBlock'])
        assert block.subnet_of(ipaddress.ip_network('10.20.0.0/16'))
        assert not any(block.overlaps(other) for other in blocks)
        blocks.append(block)
    associations = resources_of(net, 'EC2::SubnetRouteTableAssociation')
    assert len(resources_of(net, 'EC2::RouteTable')) == len(associations) == 8
    assert {v['SubnetId']['Ref'] for v in associations.values()} == expected.keys()
    assert len({v['RouteTableId']['Ref'] for v in associations.values()}) == 8
    for a in associations.values():
        assert a['RouteTableId']['Ref'] == a['SubnetId']['Ref'].replace('Subnet', 'RouteTable')
    banned = {'EC2::Route', 'EC2::InternetGateway', 'EC2::NatGateway', 'EC2::VPNGateway',
              'EC2::VPNConnection', 'EC2::TransitGateway', 'RDS::DBCluster',
              'ApiGatewayV2::Api', 'ElasticLoadBalancingV2::LoadBalancer'}
    assert not any(r['Type'].removeprefix('AWS::') in banned for t in templates for r in t['Resources'].values())
    endpoints = resources_of(net, 'EC2::VPCEndpoint')
    assert len(endpoints) == 11
    assert {p['ServiceName']['Fn::Sub'].split('}.')[1] for p in endpoints.values()} == {
        'ec2', 'ecr.api', 'ecr.dkr', 'eks', 'eks-auth', 'sqs', 'states', 'ssm', 'ssmmessages', 'secretsmanager', 's3'}
    for name, p in endpoints.items():
        assert p['PolicyDocument']['Statement']
        if name != 'S3Endpoint':
            assert p['PrivateDnsEnabled'] is True
            assert p['SubnetIds'] == [{'Ref': 'GeneralSubnetA'}, {'Ref': 'GeneralSubnetB'}]
    assert endpoints['S3Endpoint']['RouteTableIds'] == [
        {'Ref': r + 'RouteTable' + z} for r in ['General', 'Terceros', 'Onprem'] for z in 'AB']
    for sg in resources_of(net, 'EC2::SecurityGroup').values():
        assert sg['SecurityGroupEgress'] == [{'IpProtocol': 'tcp', 'FromPort': 1, 'ToPort': 1, 'CidrIp': '127.0.0.1/32'}]
    db_rules = [r for r in resources_of(net, 'EC2::SecurityGroupIngress').values()
                if r['GroupId'] == {'Ref': 'DatabaseSecurityGroup'}]
    assert len(db_rules) == 4
    assert {r['SourceSecurityGroupId']['Ref'] for r in db_rules} == {
        'GeneralNodesSecurityGroup', 'TercerosNodesSecurityGroup', 'OnpremNodesSecurityGroup', 'AdminSecurityGroup'}
    assert all(r['FromPort'] == r['ToPort'] == 5432 for r in db_rules)
    http = [r for r in resources_of(net, 'EC2::SecurityGroupIngress').values() if r['FromPort'] == 8080]
    assert len(http) == 2 and all(r['SourceSecurityGroupId'] == {'Ref': 'GeneralNodesSecurityGroup'} for r in http)
    cluster = e['Cluster']['Properties']
    assert cluster['ResourcesVpcConfig']['EndpointPublicAccess'] is False
    assert cluster['ResourcesVpcConfig']['EndpointPrivateAccess'] is True
    assert cluster['AccessConfig']['BootstrapClusterCreatorAdminPermissions'] is False
    groups = resources_of(eks, 'EKS::Nodegroup')
    assert set(groups) == {'GeneralNodes', 'TercerosNodes', 'OnpremNodes'}
    for role in ['General', 'Terceros', 'Onprem']:
        group = groups[role + 'Nodes']
        assert group['Labels'] == {'lab-role': role.lower()}
        assert group['Subnets'] == [{'Fn::ImportValue': {'Fn::Sub': '${NetworkStackName}-' + role + 'Subnet' + z + 'Id'}} for z in 'AB']
        assert group['ScalingConfig'] == {'MinSize': 1, 'DesiredSize': 1, 'MaxSize': 2}
        launch = e[role + 'LaunchTemplate']['Properties']['LaunchTemplateData']
        assert launch['SecurityGroupIds'] == [{'Fn::ImportValue': {'Fn::Sub': '${NetworkStackName}-' + role + 'NodesSecurityGroupId'}}]
        assert launch['MetadataOptions']['HttpPutResponseHopLimit'] == 1
        assert launch['MetadataOptions']['HttpTokens'] == 'required'
        if role != 'General':
            assert group['Taints'] == [{'Key': 'dedicated', 'Value': role.lower(), 'Effect': 'NO_SCHEDULE'}]
    assert not any('CNI' in str(x) for x in e['NodeRole']['Properties']['ManagedPolicyArns'])
    cni = e['VpcCni']['Properties']
    assert cni['PodIdentityAssociations'][0]['ServiceAccount'] == 'aws-node'
    assert eks['Parameters']['NetworkPolicyMode']['Default'] == 'strict'
    assert '"enableNetworkPolicy":"true"' in cni['ConfigurationValues']['Fn::Sub']
    db = s['Database']['Properties']
    assert db['Engine'] == 'postgres' and db['DBInstanceClass'] == 'db.t4g.micro'
    assert not db['MultiAZ'] and not db['PubliclyAccessible']
    assert db['StorageEncrypted'] and db['ManageMasterUserPassword']
    assert db['AllocatedStorage'] == '20' and s['Database']['DeletionPolicy'] == 'Snapshot'
    assert s['DbParameters']['Properties']['Parameters']['rds.force_ssl'] == '1'
    assert s['DbSubnetGroup']['Properties']['SubnetIds'] == [
        {'Fn::ImportValue': {'Fn::Sub': '${NetworkStackName}-DbSubnet' + z + 'Id'}} for z in 'AB']
    queue = s['WorkQueue']['Properties']
    assert queue['RedrivePolicy'] == {'deadLetterTargetArn': {'Fn::GetAtt': ['DeadLetterQueue', 'Arn']}, 'maxReceiveCount': 5}
    assert queue['MessageRetentionPeriod'] < s['DeadLetterQueue']['Properties']['MessageRetentionPeriod']
    assert queue['SqsManagedSseEnabled'] and s['DeadLetterQueue']['Properties']['SqsManagedSseEnabled']
    state = s['Dispatcher']['Properties']['Definition']['States']['Enqueue']
    assert state['Resource']['Fn::Sub'].endswith('sqs:sendMessage') and state['End']
    asl = json.loads((ROOT.parent / 'statemachine/docs-orchestrator.asl.json').read_text())
    assert asl['States']['Enqueue'] == state | {'Resource': 'arn:aws:states:::sqs:sendMessage',
        'Parameters': state['Parameters'] | {'QueueUrl': '${QUEUE_URL}'}}
    deployments = [d for d in manifests if d['kind'] == 'Deployment']
    assert len(deployments) == 3
    assert {d['spec']['template']['spec']['containers'][0]['image'] for d in deployments} == {'${DEMO_IMAGE}'}
    for d in deployments:
        ns = d['metadata']['namespace']; role = ns.removeprefix('lab-')
        pod = d['spec']['template']['spec']; container = pod['containers'][0]
        assert pod['nodeSelector'] == {'lab-role': role} and pod['serviceAccountName'] == 'micro'
        assert pod['automountServiceAccountToken'] is False
        assert pod['securityContext']['runAsNonRoot'] and container['securityContext']['readOnlyRootFilesystem']
        assert container['startupProbe']['httpGet']['path'] == '/actuator/health/liveness'
        configs = next(m['data'] for m in manifests if m['kind'] == 'ConfigMap' and m['metadata']['namespace'] == ns)
        assert 'sslmode=verify-full' in configs['DATABASE_URL'] and '/lab_' + role + '?' in configs['DATABASE_URL']
        assert configs['SPRING_SQL_INIT_MODE'] == 'never' and configs['OTEL_SDK_DISABLED'] == 'true'
        assert configs['S3_BUCKET'] == '${' + role.upper() + '_BUCKET}'
        identity = s[role.capitalize() + 'Identity']['Properties']
        assert identity['Namespace'] == ns and identity['ServiceAccount'] == 'micro'
        iam = s[role.capitalize() + 'AppRole']['Properties']
        conditions = iam['AssumeRolePolicyDocument']['Statement'][0]['Condition']['StringEquals']
        assert conditions['aws:RequestTag/kubernetes-namespace'] == ns
        assert conditions['aws:RequestTag/kubernetes-service-account'] == 'micro'
        permissions = iam['Policies'][0]['PolicyDocument']['Statement']
        assert all(p['Resource'] != '*' and p['Action'] != '*' for p in permissions)
        actions = {a for p in permissions for a in (p['Action'] if isinstance(p['Action'], list) else [p['Action']])}
        assert ('states:StartExecution' in actions) == (role == 'general')
        assert ('sqs:ReceiveMessage' in actions) == (role == 'terceros')
        policies = {m['metadata']['name']: m['spec'] for m in manifests if m['kind'] == 'NetworkPolicy' and m['metadata']['namespace'] == ns}
        assert policies['default-deny'] == {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress']}
        assert policies['micro-allowed']['policyTypes'] == ['Ingress', 'Egress']
        ingress = policies['micro-allowed']['ingress']
        if role == 'general': assert ingress == []
        else:
            assert ingress[0]['from'][0]['namespaceSelector']['matchLabels']['kubernetes.io/metadata.name'] == 'lab-general'
        if role != 'general':
            assert not any(p.get('port') == 8080 for rule in policies['micro-allowed']['egress'] for p in rule['ports'])
    assert any(m['kind'] == 'NetworkPolicy' and m['metadata']['name'] == 'coredns-required' for m in manifests)


def main():
    names = ['demo-seguros-networking', 'demo-seguros-eks', 'demo-seguros-services']
    templates, params, exports = {}, {}, {}
    for file, name in zip(sorted((ROOT / 'cloudformation').glob('*.yaml')), names, strict=True):
        template = yaml.load(file.read_text(), Loader)
        templates[name] = template
        supplied = json.loads((ROOT / 'parameters' / (file.stem + '.json')).read_text())
        supplied = {p['ParameterKey']: p['ParameterValue'] for p in supplied}
        defined = template.get('Parameters', {})
        assert supplied.keys() <= defined.keys(), f'Parametro desconocido en {file}'
        assert all(k in supplied or 'Default' in v for k, v in defined.items()), f'Parametro ausente en {file}'
        params[name] = {k: v['Default'] for k, v in defined.items() if 'Default' in v} | supplied | {'AWS::StackName': name}
        resources = template['Resources']
        for reference in refs(template):
            assert reference.startswith('AWS::') or reference in resources or reference in defined, f'Ref desconocida: {name}/{reference}'
        graph = {}
        for key, resource in resources.items():
            deps = resource.get('DependsOn', [])
            deps = [deps] if isinstance(deps, str) else deps
            assert all(d in resources for d in deps), f'DependsOn desconocido: {key}'
            graph[key] = set(deps) | (set(refs(resource)) & resources.keys())
        acyclic(graph)
        for output in template['Outputs'].values():
            export = resolve(output['Export']['Name'], params[name])
            assert export.startswith(name + '-') and export not in exports, f'Export invalido: {export}'
            exports[export] = name
    stack_graph = {name: set() for name in templates}
    for name, template in templates.items():
        for item in walk(template):
            if isinstance(item, dict) and 'Fn::ImportValue' in item:
                imported = resolve(item['Fn::ImportValue'], params[name])
                assert imported in exports, f'Import sin export: {imported}'
                stack_graph[name].add(exports[imported])
    acyclic(stack_graph)
    manifests = []
    for file in (ROOT / 'kubernetes').glob('*.yaml'):
        manifests.extend(yaml.load_all(file.read_text(), Loader))
    for file in (ROOT / 'parameters').glob('*.json'):
        json.loads(file.read_text())
    check_architecture(list(templates.values()), manifests)
    # Regression probes: network/IAM isolation checks must reject unsafe changes.
    from copy import deepcopy
    mutations = [
        (0, 'GeneralSubnetA', 'MapPublicIpOnLaunch', True),
        (1, 'Cluster', 'ResourcesVpcConfig', {'EndpointPublicAccess': True}),
        (2, 'Database', 'PubliclyAccessible', True),
    ]
    for index, resource, key, value in mutations:
        changed = deepcopy(list(templates.values()))
        changed[index]['Resources'][resource]['Properties'][key] = value
        try:
            check_architecture(changed, manifests)
        except AssertionError:
            pass
        else:
            raise AssertionError(f'No se rechazo configuracion insegura: {resource}/{key}')
    from render import render
    fixture = {'DEMO_IMAGE': '123456789012.dkr.ecr.us-east-1.amazonaws.com/micro@sha256:' + 'a' * 64,
               'DB_HOST': 'test.abc.us-east-1.rds.amazonaws.com',
               'GENERAL_BUCKET': 'test-general', 'TERCEROS_BUCKET': 'test-terceros', 'ONPREM_BUCKET': 'test-onprem'}
    assert '${' not in render(fixture)
    try:
        render(fixture | {'DEMO_IMAGE': 'nginx:latest'})
    except ValueError:
        pass
    else:
        raise AssertionError('Renderer acepto imagen externa')
    print(f'OK: {len(templates)} plantillas, {len(exports)} exports, referencias/imports y dependencias sin ciclos.')
    print('OK: ocho subredes/tablas, tres NG, endpoints, aislamiento SG/IAM/NetworkPolicy y manifiestos.')
    print('OK: RDS/SQS/Step Functions, misma imagen por rol, renderer y regresiones negativas.')
    print('Solo validacion estatica local; placeholders obligatorios pendientes, sin consultar ni modificar AWS.')


if __name__ == '__main__':
    main()
