#!/usr/bin/env python3
"""Consulta AWS sin crear ni modificar recursos; comprueba parametros y compatibilidad."""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REGION = 'us-east-1'
ADDONS = {'vpc-cni': 'VpcCniVersion', 'kube-proxy': 'KubeProxyVersion',
          'coredns': 'CoreDnsVersion', 'eks-pod-identity-agent': 'PodIdentityVersion'}
SERVICES = ['ec2', 'ecr.api', 'ecr.dkr', 'eks', 'eks-auth', 'elasticloadbalancing', 's3']


def aws(*args):
    return json.loads(subprocess.check_output(
        ['aws', *args, '--region', REGION, '--output', 'json', '--no-cli-pager'], text=True))


def check_admin(parameters):
    cidr = ipaddress.ip_network(parameters['AdminPublicCidr'], strict=True)
    if cidr.version != 4 or cidr.prefixlen != 32 or not cidr.network_address.is_global:
        raise ValueError('AdminPublicCidr debe ser IPv4 publica /32')
    if not re.fullmatch(r'arn:aws:iam::[0-9]{12}:(role|user)/[A-Za-z0-9+=,.@_/-]+',
                        parameters['AdminPrincipalArn']):
        raise ValueError('AdminPrincipalArn debe ser ARN IAM persistente, no sesion STS')


def verify(parameters):
    version = parameters['KubernetesVersion']
    versions = aws('eks', 'describe-cluster-versions', '--cluster-versions', version)['clusterVersions']
    if len(versions) != 1 or versions[0]['versionStatus'] != 'STANDARD_SUPPORT':
        raise ValueError(f'EKS {version} no esta en soporte estandar')
    report = {'checkedAtUtc': datetime.now(timezone.utc).isoformat(), 'region': REGION,
              'kubernetesVersion': version, 'status': versions[0]['versionStatus'],
              'endOfStandardSupportDate': versions[0]['endOfStandardSupportDate'], 'addons': {}}
    for name, key in ADDONS.items():
        available = aws('eks', 'describe-addon-versions', '--kubernetes-version', version,
                        '--addon-name', name)['addons']
        compatible = [v for addon in available for v in addon['addonVersions']
                      if v['addonVersion'] == parameters[key] and 'amd64' in v['architecture']
                      and 'ec2' in v.get('computeTypes', [])
                      and any(c['clusterVersion'] == version for c in v['compatibilities'])]
        if not compatible:
            raise ValueError(f'{name} {parameters[key]} no es compatible con EKS {version}/amd64/EC2')
        report['addons'][name] = compatible[0]
    recommended = json.loads(aws('ssm', 'get-parameter', '--name',
        f'/aws/service/eks/optimized-ami/{version}/amazon-linux-2023/x86_64/standard/recommended')['Parameter']['Value'])
    if parameters['NodeReleaseVersion'] != recommended['release_version']:
        raise ValueError('Release fijado difiere del recomendado: revisar antes de actualizar parametros. '
                         f'Recomendado: {recommended["release_version"]}')
    ami = aws('ec2', 'describe-images', '--image-ids', recommended['image_id'])['Images'][0]
    if ami['State'] != 'available' or ami['Architecture'] != 'x86_64':
        raise ValueError('AMI no disponible o arquitectura incorrecta')
    report['ami'] = {**recommended, 'state': ami['State'], 'architecture': ami['Architecture']}
    details = aws('ec2', 'describe-vpc-endpoint-services', '--service-names',
                  *[f'com.amazonaws.{REGION}.{s}' for s in SERVICES])['ServiceDetails']
    # S3 publica Gateway e Interface con el mismo nombre; este lab usa Gateway.
    details = [d for d in details if any(t['ServiceType'] ==
               ('Gateway' if d['ServiceName'].endswith('.s3') else 'Interface')
               for t in d['ServiceType'])]
    if len(details) != len(SERVICES):
        raise ValueError('Faltan servicios de endpoints')
    report['endpoints'] = []
    for endpoint in details:
        if not {'us-east-1a', 'us-east-1b'}.issubset(endpoint['AvailabilityZones']):
            raise ValueError(f'Endpoint no disponible en ambas AZ: {endpoint["ServiceName"]}')
        if not endpoint['VpcEndpointPolicySupported']:
            raise ValueError(f'Endpoint no acepta politica: {endpoint["ServiceName"]}')
        report['endpoints'].append({k: endpoint[k] for k in
                                   ['ServiceName', 'AvailabilityZones', 'VpcEndpointPolicySupported']})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parameters', type=Path, default=ROOT / 'parameters/02-eks.json')
    parser.add_argument('--compatibility-only', action='store_true',
                        help='Solo catalogos AWS; no valida identidad ni IP. No habilita despliegue.')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    parameters = {p['ParameterKey']: p['ParameterValue'] for p in json.loads(args.parameters.read_text())}
    if not args.compatibility_only:
        check_admin(parameters)
    report = verify(parameters)
    report['adminParametersValidated'] = not args.compatibility_only
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('OK: soporte EKS, addons amd64/EC2, AMI AL2023 y endpoints en ambas AZ.')
    if args.compatibility_only:
        print('PENDIENTE: IP /32 y ARN IAM reales; ejecutar sin --compatibility-only antes de desplegar.')


if __name__ == '__main__':
    main()
