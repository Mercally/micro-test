#!/usr/bin/env python3
"""Static checks for the new-account foundation; no AWS calls."""
from copy import deepcopy
import ipaddress
from pathlib import Path
import runpy
import yaml

ROOT = Path(__file__).resolve().parent
helpers = runpy.run_path(str(ROOT.parent / 'aws/scripts/validate.py'))


def check(templates):
    network, database = templates
    exports = set()
    for template, stack in zip(templates, ['micro-test-network', 'micro-test-database']):
        resources = template['Resources']
        params = template['Parameters']
        for name in helpers['refs'](template):
            assert name.startswith('AWS::') or name in resources or name in params, name
        graph = {}
        for name, resource in resources.items():
            deps = resource.get('DependsOn', [])
            deps = [deps] if isinstance(deps, str) else deps
            assert all(dep in resources for dep in deps)
            graph[name] = set(deps) | (set(helpers['refs'](resource)) & resources.keys())
        helpers['acyclic'](graph)
        for output in template['Outputs'].values():
            name = helpers['resolve'](output['Export']['Name'], {'AWS::StackName': stack})
            assert name not in exports
            exports.add(name)
    for item in helpers['walk'](database):
        if isinstance(item, dict) and 'Fn::ImportValue' in item:
            assert helpers['resolve'](item['Fn::ImportValue'], {'NetworkStackName':'micro-test-network'}) in exports
    resources = network['Resources']
    allowed = {'VPC', 'Subnet', 'RouteTable', 'SubnetRouteTableAssociation',
               'SecurityGroup', 'SecurityGroupIngress', 'SecurityGroupEgress'}
    assert all(r['Type'] in {'AWS::EC2::' + k for k in allowed} for r in resources.values())
    of = helpers['resources_of']
    subnets = of(network, 'EC2::Subnet')
    assert len(subnets) == 8
    blocks = []
    for name, subnet in subnets.items():
        assert subnet['MapPublicIpOnLaunch'] is False
        assert subnet['AvailabilityZoneId'] == {'Ref': 'AvailabilityZoneId' + name[-1]}
        block = ipaddress.ip_network(helpers['resolve'](subnet['CidrBlock'], {'VpcSecondOctet':'20'}))
        assert block.subnet_of(ipaddress.ip_network('10.20.0.0/16'))
        assert all(not block.overlaps(other) for other in blocks)
        blocks.append(block)
    associations = of(network, 'EC2::SubnetRouteTableAssociation')
    assert len(of(network, 'EC2::RouteTable')) == len(associations) == 8
    assert {a['SubnetId']['Ref'] for a in associations.values()} == set(subnets)
    assert len({a['RouteTableId']['Ref'] for a in associations.values()}) == 8
    for sg in of(network, 'EC2::SecurityGroup').values():
        assert not sg.get('SecurityGroupIngress')
        assert sg['SecurityGroupEgress'] == [{'IpProtocol':'tcp','FromPort':1,'ToPort':1,'CidrIp':'127.0.0.1/32'}]
    ingress = list(of(network, 'EC2::SecurityGroupIngress').values())
    egress = list(of(network, 'EC2::SecurityGroupEgress').values())
    assert len(ingress) == len(egress) == 2
    assert {x['SourceSecurityGroupId']['Ref'] for x in ingress} == {'AdminSecurityGroup', 'DatabaseClientSecurityGroup'}
    for rule in ingress:
        assert rule['GroupId'] == {'Ref':'DatabaseSecurityGroup'}
        assert rule['IpProtocol'] == 'tcp' and rule['FromPort'] == rule['ToPort'] == 5432
        assert any(e['GroupId'] == rule['SourceSecurityGroupId'] and
                   e['DestinationSecurityGroupId'] == rule['GroupId'] and
                   e['IpProtocol'] == 'tcp' and e['FromPort'] == e['ToPort'] == 5432 for e in egress)
    db = database['Resources']['Database']
    p = db['Properties']
    assert p['Engine'] == 'postgres' and p['PubliclyAccessible'] is False
    assert p['StorageEncrypted'] and p['ManageMasterUserPassword']
    assert 'MasterUserPassword' not in p
    assert db['DeletionPolicy'] == db['UpdateReplacePolicy'] == 'Snapshot'
    assert database['Resources']['DbParameters']['Properties']['Parameters']['rds.force_ssl'] == '1'
    assert database['Parameters']['BackupRetentionDays']['MinValue'] >= 1
    assert p['VPCSecurityGroups'] == [{'Fn::ImportValue': {'Fn::Sub':'${NetworkStackName}-DatabaseSecurityGroupId'}}]
    assert database['Resources']['DbSubnetGroup']['Properties']['SubnetIds'] == [
        {'Fn::ImportValue': {'Fn::Sub':'${NetworkStackName}-DbSubnet'+z+'Id'}} for z in 'AB']


def main():
    templates = [yaml.load((ROOT / name).read_text(), helpers['Loader']) for name in
                 ['01-network-general.yaml', '02-database.yaml']]
    check(templates)
    for index, resource, key, value in [
        (0, 'GeneralSubnetA', 'MapPublicIpOnLaunch', True),
        (0, 'AdminDatabaseIngress', 'FromPort', 1),
        (1, 'Database', 'PubliclyAccessible', True),
        (1, 'Database', 'StorageEncrypted', False),
    ]:
        changed = deepcopy(templates)
        changed[index]['Resources'][resource]['Properties'][key] = value
        try:
            check(changed)
        except AssertionError:
            continue
        raise AssertionError(f'Unsafe mutation accepted: {resource}.{key}')
    print('OK: two templates; references, dependency cycles, exports/imports, CIDRs, SG and RDS controls.')
    print('OK: four unsafe mutations rejected. Static checks only; no AWS access.')


if __name__ == '__main__':
    main()
