#!/usr/bin/env python3
"""Renderiza demo.yaml localmente; no lee credenciales ni contacta AWS/Kubernetes."""
import argparse
import json
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
KEYS = {'DEMO_IMAGE', 'DB_HOST', 'GENERAL_BUCKET', 'TERCEROS_BUCKET', 'ONPREM_BUCKET'}


def render(values):
    if set(values) != KEYS or not all(isinstance(v, str) for v in values.values()):
        raise ValueError(f'Usar exactamente estas claves de texto: {sorted(KEYS)}')
    if not re.fullmatch(r'[0-9]{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com/[a-z0-9._/-]+@sha256:[a-f0-9]{64}', values['DEMO_IMAGE']):
        raise ValueError('DEMO_IMAGE debe ser ECR privado us-east-1 con digest sha256 real')
    if not re.fullmatch(r'[a-zA-Z0-9.-]+\.us-east-1\.rds\.amazonaws\.com', values['DB_HOST']):
        raise ValueError('DB_HOST debe ser endpoint RDS real de us-east-1')
    for key in KEYS - {'DEMO_IMAGE', 'DB_HOST'}:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', values[key]):
            raise ValueError(f'Nombre de bucket invalido: {key}')
    if len({values[k] for k in KEYS if k.endswith('_BUCKET')}) != 3:
        raise ValueError('Los tres roles requieren buckets distintos')
    text = (ROOT / 'kubernetes/demo.yaml').read_text()
    text = re.sub(r'\$\{([A-Z_]+)\}', lambda m: values[m[1]], text)
    if '${' in text:
        raise ValueError('Placeholder sin resolver')
    list(yaml.safe_load_all(text))
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('values', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT / 'kubernetes'):
        parser.error('Guardar render fuera de kubernetes/; conservar fuente con placeholders')
    args.output.write_text(render(json.loads(args.values.read_text())))
    print(f'Manifiesto local: {args.output}. No aplicado.')


if __name__ == '__main__':
    main()
