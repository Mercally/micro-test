#!/usr/bin/env python3
"""Validacion local reproducible: YAML/JSON, contratos CFN, rutas, CIDR y controles de acceso."""
import ipaddress
import json
from pathlib import Path
import re
import yaml
from preflight import check_admin

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
            assert isinstance(item['Fn::Sub'], str), 'Ampliar validador para Fn::Sub con mapa'
            for name in re.findall(r'\$\{([^}]+)\}', item['Fn::Sub']):
                if not name.startswith('!'):
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


def main():
    names = ['demo-seguros-networking', 'demo-seguros-eks', 'demo-seguros-ingress', 'demo-seguros-api']
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
        for ref in refs(template):
            assert ref.startswith('AWS::') or ref in resources or ref in defined, f'Ref desconocida: {name}/{ref}'
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
    net = templates[names[0]]['Resources']
    expected = {'PublicSubnetA': '10.20.0.0/24', 'PublicSubnetB': '10.20.1.0/24',
                'EKSPrivateSubnetA': '10.20.16.0/20', 'EKSPrivateSubnetB': '10.20.32.0/20',
                'DataSubnetA': '10.20.64.0/24', 'DataSubnetB': '10.20.65.0/24'}
    subnets = {k: v['Properties'] for k, v in net.items() if v['Type'] == 'AWS::EC2::Subnet'}
    assert subnets.keys() == expected.keys()
    vpc = ipaddress.ip_network(net['VPC']['Properties']['CidrBlock'])
    blocks = []
    for key, cidr in expected.items():
        subnet = subnets[key]
        assert subnet['CidrBlock'] == cidr
        assert subnet['AvailabilityZone'] == ('us-east-1a' if key.endswith('A') else 'us-east-1b')
        assert subnet['MapPublicIpOnLaunch'] == key.startswith('Public')
        block = ipaddress.ip_network(cidr)
        assert block.subnet_of(vpc) and all(not block.overlaps(other) for other in blocks)
        assert block.num_addresses - 5 == (4091 if 'EKS' in key else 251)
        blocks.append(block)
    associations = [r['Properties'] for r in net.values() if r['Type'] == 'AWS::EC2::SubnetRouteTableAssociation']
    assert len(associations) == 6
    assert {r['SubnetId']['Ref'] for r in associations} == expected.keys()
    for association in associations:
        subnet = association['SubnetId']['Ref']
        table = ('PublicRouteTable' if subnet.startswith('Public') else
                 ('EKSPrivateRouteTable' if subnet.startswith('EKS') else 'DataRouteTable') + subnet[-1])
        assert association['RouteTableId'] == {'Ref': table}
    routes = [r['Properties'] for r in net.values() if r['Type'] == 'AWS::EC2::Route']
    assert routes == [{'RouteTableId': {'Ref': 'PublicRouteTable'}, 'DestinationCidrBlock': '0.0.0.0/0',
                       'GatewayId': {'Ref': 'InternetGateway'}}]
    assert not any(r['Type'] == 'AWS::EC2::NatGateway' for r in net.values())
    endpoints = [r['Properties'] for r in net.values() if r['Type'] == 'AWS::EC2::VPCEndpoint']
    assert len(endpoints) == 7
    for endpoint in endpoints:
        assert endpoint['PolicyDocument']['Statement']
        if endpoint['VpcEndpointType'] == 'Interface':
            assert endpoint['PrivateDnsEnabled'] is True
            assert endpoint['SubnetIds'] == [{'Ref': 'EKSPrivateSubnetA'}, {'Ref': 'EKSPrivateSubnetB'}]
            assert endpoint['SecurityGroupIds'] == [{'Ref': 'EndpointSecurityGroup'}]
        else:
            assert endpoint['RouteTableIds'] == [{'Ref': 'EKSPrivateRouteTableA'}, {'Ref': 'EKSPrivateRouteTableB'}]
    eks = templates[names[1]]
    for key in ['AdminPublicCidr', 'AdminPrincipalArn']:
        assert 'Default' not in eks['Parameters'][key]
    cidr_pattern = eks['Parameters']['AdminPublicCidr']['AllowedPattern']
    assert re.fullmatch(cidr_pattern, '8.8.8.8/32')
    assert not re.fullmatch(cidr_pattern, '0.0.0.0/0')
    assert not re.fullmatch(cidr_pattern, '999.1.1.1/32')
    valid = {'AdminPublicCidr': '8.8.8.8/32', 'AdminPrincipalArn': 'arn:aws:iam::123456789012:role/Admin'}
    check_admin(valid)
    for bad in ['0.0.0.0/0', '10.20.0.1/32', '8.8.8.8/24', '::1/128']:
        try:
            check_admin(valid | {'AdminPublicCidr': bad})
        except ValueError:
            pass
        else:
            raise AssertionError(f'CIDR inseguro aceptado: {bad}')
    cluster = eks['Resources']['Cluster']['Properties']
    # El export CSV de red no puede usarse literalmente como valor de un tag EKS.
    connectivity = next(t['Value'] for t in cluster['Tags'] if t['Key'] == 'PrivateConnectivity')
    assert 'Fn::Join' in connectivity, 'PrivateConnectivity debe convertir el CSV a caracteres permitidos por EKS'
    separator, parts = connectivity['Fn::Join']
    delimiter, imported = parts['Fn::Split']
    assert imported == {'Fn::ImportValue': {'Fn::Sub': '${NetworkStackName}-PrivateConnectivityReady'}}
    export_separator, endpoints = templates[names[0]]['Outputs']['PrivateConnectivityReady']['Value']['Fn::Join']
    endpoint_ids = [f'vpce-{i:017x}' for i in range(len(endpoints))]
    tag_value = separator.join(export_separator.join(endpoint_ids).split(delimiter))
    assert len(tag_value) <= 256 and re.fullmatch(r'[A-Za-z0-9 _.:/=+@-]*', tag_value), 'Tag EKS invalido'
    assert all(endpoint_id in tag_value for endpoint_id in endpoint_ids)
    assert cluster['AccessConfig'] == {'AuthenticationMode': 'API', 'BootstrapClusterCreatorAdminPermissions': False}
    assert cluster['ResourcesVpcConfig']['PublicAccessCidrs'] == [{'Ref': 'AdminPublicCidr'}]
    assert cluster['ResourcesVpcConfig']['EndpointPrivateAccess'] is True
    assert not ipaddress.ip_network(cluster['KubernetesNetworkConfig']['ServiceIpv4Cidr']).overlaps(vpc)
    for key in ['NodesA', 'NodesB']:
        node = eks['Resources'][key]['Properties']
        assert node['AmiType'] == 'AL2023_x86_64_STANDARD'
        assert node['Subnets'][0]['Fn::ImportValue']['Fn::Sub'].endswith(f'EKSPrivateSubnet{key[-1]}Id')
    ingress = templates[names[2]]['Resources']
    assert ingress['Nlb']['Properties']['Scheme'] == 'internal'
    assert ingress['TargetGroup']['Properties']['TargetType'] == 'ip'
    assert 'Targets' not in ingress['TargetGroup']['Properties']
    policy = ingress['ControllerRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
    assert policy[1]['Action'] == ['elasticloadbalancing:RegisterTargets', 'elasticloadbalancing:DeregisterTargets']
    assert policy[1]['Resource'] == {'Ref': 'TargetGroup'}
    assert templates[names[3]]['Resources']['Route']['Properties']['AuthorizationType'] == 'AWS_IAM'
    demo = list(yaml.load_all((ROOT / 'kubernetes/demo.yaml').read_text(), Loader))
    service = next(d for d in demo if d['kind'] == 'Service')
    assert service['spec']['type'] == 'ClusterIP'
    binding = yaml.load((ROOT / 'kubernetes/target-group-binding.yaml').read_text(), Loader)
    assert binding['spec']['serviceRef'] == {'name': service['metadata']['name'], 'port': service['spec']['ports'][0]['port']}
    assert 'networking' not in binding['spec']
    values = yaml.load((ROOT / 'helm/controller-values.yaml').read_text(), Loader)
    assert all(values[k] is False for k in ['enableShield', 'enableWaf', 'enableWafv2', 'enableBackendSecurityGroup'])
    print(f'OK: {len(templates)} plantillas, {len(exports)} exports, referencias y dependencias sin ciclos.')
    print('OK: seis CIDR exactos sin solapamientos, rutas, endpoints, acceso admin, IAM y contrato Service/TGB.')
    print('OK: tag EKS PrivateConnectivity sin caracteres invalidos y con import de red conservado.')
    print('No se consulto ni modifico AWS. Validar IP y ARN reales con preflight.py antes de desplegar.')


if __name__ == '__main__':
    main()
