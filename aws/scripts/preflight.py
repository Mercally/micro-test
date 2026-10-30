#!/usr/bin/env python3
"""Preflight opcional de lectura AWS. No crea recursos ni ejecuta comandos kubectl."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REGION = 'us-east-1'
ADDONS = {'vpc-cni': 'VpcCniVersion', 'kube-proxy': 'KubeProxyVersion',
          'coredns': 'CoreDnsVersion', 'eks-pod-identity-agent': 'PodIdentityVersion'}
SERVICES = ['ec2', 'ecr.api', 'ecr.dkr', 'eks', 'eks-auth', 'sqs', 'states', 'ssm', 'ssmmessages', 'secretsmanager', 's3']


def aws(*args):
    return json.loads(subprocess.check_output(
        ['aws', *args, '--region', REGION, '--output', 'json', '--no-cli-pager'], text=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parameters-dir', type=Path, default=ROOT / 'parameters')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    parameters = {}
    for name in ['01-network', '02-eks', '03-services']:
        for p in json.loads((args.parameters_dir / (name + '.json')).read_text()):
            parameters[p['ParameterKey']] = p['ParameterValue']
    missing = [key for key, value in parameters.items() if value.startswith('REQUIRED_')]
    if missing:
        parser.error('Completar parametros reales antes de consultar AWS: ' + ', '.join(missing))
    if not re.fullmatch(r'arn:aws:iam::[0-9]{12}:(role|user)/[A-Za-z0-9+=,.@_/-]+', parameters['AdminPrincipalArn']):
        parser.error('AdminPrincipalArn debe ser ARN IAM persistente, no sesion STS')
    if parameters['NetworkPolicyMode'] != 'strict':
        parser.error('Configuracion final debe usar strict; standard solo durante bootstrap sin aplicaciones')
    version = parameters['KubernetesVersion']
    versions = aws('eks', 'describe-cluster-versions', '--cluster-versions', version)['clusterVersions']
    if len(versions) != 1 or versions[0]['versionStatus'] != 'STANDARD_SUPPORT':
        raise ValueError(f'EKS {version} fuera de soporte estandar')
    report = {'checkedAtUtc': datetime.now(timezone.utc).isoformat(), 'region': REGION,
              'kubernetesVersion': version, 'status': versions[0]['versionStatus'], 'addons': {}}
    for name, key in ADDONS.items():
        available = aws('eks', 'describe-addon-versions', '--kubernetes-version', version, '--addon-name', name)['addons']
        compatible = [v for addon in available for v in addon['addonVersions']
                      if v['addonVersion'] == parameters[key] and 'amd64' in v['architecture']
                      and 'ec2' in v.get('computeTypes', [])
                      and any(c['clusterVersion'] == version for c in v['compatibilities'])]
        if not compatible:
            raise ValueError(f'{name} no compatible con EKS {version}/amd64/EC2')
        report['addons'][name] = compatible[0]
    config = aws('eks', 'describe-addon-configuration', '--addon-name', 'vpc-cni',
                 '--addon-version', parameters['VpcCniVersion'])
    for key in ['enableNetworkPolicy', 'NETWORK_POLICY_ENFORCING_MODE']:
        if key not in config['configurationSchema']:
            raise ValueError(f'CNI no admite {key}')
    report['cniPodIdentity'] = config.get('podIdentityConfiguration', [])
    if not any(p['serviceAccount'] == 'aws-node' for p in report['cniPodIdentity']):
        raise ValueError('CNI seleccionado no declara Pod Identity para aws-node')
    recommended = json.loads(aws('ssm', 'get-parameter', '--name',
        f'/aws/service/eks/optimized-ami/{version}/amazon-linux-2023/x86_64/standard/recommended')['Parameter']['Value'])
    if parameters['NodeReleaseVersion'] != recommended['release_version']:
        raise ValueError('Release difiere del recomendado: revisar explicitamente; no actualizar automaticamente')
    image = aws('ec2', 'describe-images', '--image-ids', recommended['image_id'])['Images'][0]
    if image['State'] != 'available' or image['Architecture'] != 'x86_64':
        raise ValueError('AMI no disponible/amd64')
    report['ami'] = recommended
    offers = aws('ec2', 'describe-instance-type-offerings', '--location-type', 'availability-zone',
                 '--filters', 'Name=instance-type,Values=t3.medium')['InstanceTypeOfferings']
    if not {'us-east-1a', 'us-east-1b'} <= {o['Location'] for o in offers}:
        raise ValueError('t3.medium no ofertado en ambas AZ; no equivale a capacidad garantizada')
    details = aws('ec2', 'describe-vpc-endpoint-services', '--service-names',
                  *[f'com.amazonaws.{REGION}.{s}' for s in SERVICES])['ServiceDetails']
    details = [d for d in details if any(t['ServiceType'] ==
               ('Gateway' if d['ServiceName'].endswith('.s3') else 'Interface') for t in d['ServiceType'])]
    if len(details) != len(SERVICES):
        raise ValueError('Faltan servicios de endpoints')
    for d in details:
        if not {'us-east-1a', 'us-east-1b'} <= set(d['AvailabilityZones']) or not d['VpcEndpointPolicySupported']:
            raise ValueError('Endpoint no disponible en ambas AZ o sin politicas: ' + d['ServiceName'])
    report['endpoints'] = [d['ServiceName'] for d in details]
    lists = aws('ec2', 'describe-managed-prefix-lists', '--filters',
                f'Name=prefix-list-name,Values=com.amazonaws.{REGION}.s3')['PrefixLists']
    if not any(p['PrefixListId'] == parameters['S3PrefixListId'] for p in lists):
        raise ValueError('S3PrefixListId no corresponde a S3 us-east-1')
    engine = parameters['PostgresEngineVersion']
    engines = aws('rds', 'describe-db-engine-versions', '--engine', 'postgres', '--engine-version', engine)['DBEngineVersions']
    if len(engines) != 1 or engines[0]['DBParameterGroupFamily'] != parameters['PostgresParameterFamily']:
        raise ValueError('Version/familia PostgreSQL incompatibles')
    options = aws('rds', 'describe-orderable-db-instance-options', '--engine', 'postgres',
                  '--engine-version', engine, '--db-instance-class', 'db.t4g.micro')['OrderableDBInstanceOptions']
    options = [o for o in options if o['StorageType'] == 'gp3' and o['MinStorageSize'] <= 20 <= o['MaxStorageSize']
               and {'us-east-1a', 'us-east-1b'} <= {a['Name'] for a in o['AvailabilityZones']}]
    if not options:
        raise ValueError('PostgreSQL/db.t4g.micro/gp3 20 GiB no disponible en ambas AZ')
    report['database'] = {'engine': engine, 'family': engines[0]['DBParameterGroupFamily'], 'class': 'db.t4g.micro'}
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('OK: catalogos EKS/addons/AMI, EC2, endpoints, prefix list S3 y opciones RDS.')
    print('Pendiente: soporte estandar RDS, cuotas, SCP/IAM efectivos, presupuesto, digest y pruebas en recursos reales.')


if __name__ == '__main__':
    main()
