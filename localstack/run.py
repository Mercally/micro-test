#!/usr/bin/env python3
"""Canonical LocalStack lab entry point; bootstrap owns only its own new emulator."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'business'), str(ROOT / 'scripts')]
from business import Lab, endpoint, report_dir, save, public_report
from kube import DOCKER, Kube, run
import yaml

ENDPOINT = 'http://127.0.0.1:4566'
NAME = 'micro-test-local-emulator'
OWNER = 'localstack/lab-v1'
STAGES = ('emulator', 'foundation', 'kubernetes', 'registry', 'nlb', 'business',
          'hpa', 'karpenter', 'node-test', 'purchase-test', 'http-file')


def docker_containers():
    ids = run(DOCKER + ['ps', '-aq', '--no-trunc']).stdout.split()
    return json.loads(run(DOCKER + ['inspect'] + ids).stdout) if ids else []


def owned_emulator(containers):
    """Fail before any mutation if another lab or a conflicting container exists."""
    selected = None
    for item in containers:
        name = item['Name'].lstrip('/')
        bindings = item.get('HostConfig', {}).get('PortBindings') or {}
        publishes = any(p.get('HostPort') == '4566' for ports in bindings.values() for p in ports or [])
        is_localstack = item['Config']['Image'].startswith('localstack/localstack')
        if name == NAME:
            labels = item['Config'].get('Labels') or {}
            env = dict(v.split('=', 1) for v in item['Config'].get('Env', []) if '=' in v)
            mounted = any(m.get('Type') == 'volume' and m.get('Destination') == '/var/lib/localstack'
                          and m.get('Name') == 'micro-test-local_state' for m in item.get('Mounts', []))
            expected = yaml.safe_load((ROOT / 'compose.yaml').read_text())['services']['localstack']['image']
            if (labels.get('lab.launcher') != OWNER or labels.get('com.docker.compose.project') != 'micro-test-local'
                    or env.get('PERSISTENCE') != '1' or not mounted or item['Config']['Image'] != expected
                    or not publishes):
                raise ValueError('Existing emulator does not match owned persistent configuration; preserve it')
            selected = item
        elif publishes or is_localstack:
            raise ValueError('Existing emulator/port 4566 preserved: ' + name
                             + '. Use an isolated engine, or review localstack/CLEANUP.md before deliberately replacing this lab.')
    return selected


def require_wsl():
    if (sys.platform != 'linux' or not Path('/proc/sys/kernel/osrelease').exists()
            or 'microsoft' not in Path('/proc/sys/kernel/osrelease').read_text().lower()
            or platform.machine() not in ('x86_64', 'AMD64')):
        raise ValueError('Fresh bootstrap requires x86_64 Windows/WSL2 with Docker Desktop integration')
    for name in ('docker', 'curl'):
        if not shutil.which(name):
            raise ValueError('Missing prerequisite: ' + name + '; see localstack/README.md')
    # Every Docker command is pinned to the local socket, including k3d children.
    os.environ.pop('DOCKER_CONTEXT', None)
    os.environ['DOCKER_HOST'] = 'unix:///var/run/docker.sock'
    if run(DOCKER + ['info', '--format', '{{.OSType}}']).stdout.strip() != 'linux':
        raise ValueError('Docker must run Linux containers')
    run(DOCKER + ['compose', 'version'])


def ensure_emulator():
    current = owned_emulator(docker_containers())
    if current:
        if not current['State']['Running']:
            run(DOCKER + ['start', current['Id']])
    else:
        # Docker can be free while another host process holds the port.
        with socket.socket() as check:
            check.bind(('127.0.0.1', 4566))
        if not os.environ.get('LOCALSTACK_AUTH_TOKEN'):
            raise ValueError('LOCALSTACK_AUTH_TOKEN required in shell; never commit token')
        volumes = run(DOCKER + ['volume', 'ls', '--filter', 'name=^micro-test-local_state$', '-q']).stdout.split()
        if volumes:
            volume = json.loads(run(DOCKER + ['volume', 'inspect', volumes[0]]).stdout)[0]
            if volume.get('Labels', {}).get('lab.launcher') != OWNER:
                raise ValueError('Existing volume is not owned by this launcher; preserve it')
        # --no-recreate is deliberate: never replaces an existing emulator/data.
        run(DOCKER + ['compose', '-f', str(ROOT / 'compose.yaml'), 'up', '-d', '--no-recreate'])
    deadline = time.monotonic() + 180
    from common import http
    while time.monotonic() < deadline:
        try:
            info = http(ENDPOINT + '/_localstack/info')
            if info.get('edition') == 'pro' and info.get('is_license_activated'):
                current = owned_emulator(docker_containers())
                return current
        except (OSError, ValueError):
            pass
        time.sleep(3)
    raise TimeoutError('LocalStack Pro/license did not become ready; inspect container logs locally')


def cluster_runtime(lab, emulator):
    from wsl import runtime_binding
    # Match worker instance IDs against owned EKS, not merely a Docker name prefix.
    eks_name = lab.outputs('eks')['ClusterName']
    candidates = []
    for cluster in json.loads(run(['k3d', 'cluster', 'list', '-o', 'json']).stdout):
        if not cluster['name'].startswith('micro-test-local-'):
            continue
        network = cluster.get('network', {}).get('name')
        try:
            binding = runtime_binding(lab, eks_name, cluster, network, docker_containers())
            candidates.append((cluster, network, binding))
        except ValueError:
            continue
    if len(candidates) != 1:
        raise ValueError('Expected one k3d cluster bound to owned EKS workers; inspect local containers')
    cluster, network, binding = candidates[0]
    current = json.loads(run(DOCKER + ['inspect', emulator['Id']]).stdout)[0]
    if network not in current['NetworkSettings']['Networks']:
        run(DOCKER + ['network', 'connect', '--gw-priority', '-1', network, emulator['Id']])
    kube = Kube(cluster['name'], expected_network=network, expected_api_port=binding['apiPort'])
    deadline = time.monotonic() + 180
    while True:
        try:
            kube.ready()
            break
        except (ValueError, RuntimeError):
            if time.monotonic() >= deadline: raise
            time.sleep(3)
    return kube, network, eks_name


def pause_and_drain(lab):
    from business import pipe, publisher_schedule
    if not lab.stack('micro-test-local-business'):
        return
    publisher_schedule(lab, False)
    pipe(lab, 'STOPPED')
    states = lab.client('stepfunctions')
    deadline = time.monotonic() + 270
    while states.list_executions(stateMachineArn=lab.outputs('business')['StateMachineArn'],
                                 statusFilter='RUNNING', maxResults=1)['executions']:
        if time.monotonic() >= deadline:
            raise TimeoutError('Active workflows did not drain; triggers remain stopped')
        time.sleep(3)


def scaling_modules():
    import importlib.util
    result = []
    for name in ('flow', 'runtime', 'karpenter'):
        spec = importlib.util.spec_from_file_location('bootstrap_' + name, ROOT / 'scaling' / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result.append(module)
    return result


def bootstrap(args):
    from business import deploy, pipe, publisher_schedule, invoke, smoke, http_file
    from kube import bootstrap as kube_bootstrap, registry
    from lambda_network import prepare
    from tools import install
    from lab import deploy as foundation
    flow, scaling, karpenter = scaling_modules()
    report = report_dir() / 'bootstrap.json'
    result = {'status': 'RUNNING', 'endpoint': ENDPOINT, 'stages': {},
              'persistenceRecovery': 'NOT_TESTED: configured volume is not restart proof'}
    def stage(name, action):
        print('Stage: ' + name, flush=True)
        result['currentStage'] = name
        save(report, result)
        value = action()
        if isinstance(value, dict):
            save(report.parent / (name + '.json'), public_report(value))
        if isinstance(value, dict) and value.get('status') == 'FAIL':
            raise RuntimeError(name + ' failed: inspect stage report')
        result['stages'][name] = 'PASS'
        save(report, result)
        return value
    lab = None
    try:
        require_wsl()
        # Ownership check precedes downloads, container creation and AWS calls.
        owned_emulator(docker_containers())
        install(ROOT)
        def emulator_stage():
            current = ensure_emulator()
            return {'id': current['Id'], 'persistence': 'configured'}
        emulator = stage('emulator', emulator_stage)
        emulator = {'Id': emulator['id']}
        lab = Lab(ENDPOINT)
        pause_and_drain(lab)
        stage('foundation', lambda: foundation(lab))
        kube, network, eks_name = cluster_runtime(lab, emulator)
        result['cluster'] = kube.cluster
        result['network'] = network
        stage('kubernetes', lambda: kube_bootstrap(lab, kube, argparse.Namespace(
            localstack_container=emulator['Id'], docker_network=network,
            database='rds', presign_endpoint=args.client_endpoint)))
        stage('registry', lambda: registry(lab, kube.report_dir))
        prepare(lab, kube, emulator['Id'], network, ROOT / '.state/business-runtime.json')
        def nlb():
            flow.deploy(lab)
            return scaling.sync_targets(lab, kube, 'bridge', 'nodeport')
        stage('nlb', nlb)
        stage('business', lambda: deploy(lab, argparse.Namespace(
            runtime=str(ROOT / '.state/business-runtime.json'), update=True)))
        def hpa():
            # Metrics-server needs a collection interval after new pods start.
            deadline = time.monotonic() + 120
            while True:
                try: return scaling.install_hpa(kube)
                except (RuntimeError, ValueError) as exc:
                    if 'metrics' not in str(exc).lower() or time.monotonic() >= deadline: raise
                    time.sleep(5)
        stage('hpa', hpa)
        local = json.loads(run(DOCKER + ['inspect', emulator['Id']]).stdout)[0]
        stage('karpenter', lambda: karpenter.install(lab, kube, local, eks_name, network))
        node = stage('node-test', lambda: karpenter.node_test(lab, kube))
        save(kube.report_dir / 'karpenter-node-test.json', node)
        if not invoke(lab, 'publisher', {'health': True})['schemaReady']:
            raise ValueError('Outbox schema is not ready')
        pipe(lab, 'RUNNING')
        publisher_schedule(lab, True)
        proof = {}
        stage('purchase-test', lambda: smoke(lab, proof))
        save(report.parent / 'purchase-test.json', public_report(proof))
        client_lab = lab if args.client_endpoint == ENDPOINT else Lab(args.client_endpoint)
        stage('http-file', lambda: http_file(client_lab))
        result['status'] = 'PASS'
        result['scope'] = 'Fresh local runtime; HPA installed with CPU/RAM samples; real Karpenter worker and purchase/PDF. Scaling under load/restart recovery still require exercises.'
    except Exception as exc:
        result.update(status='FAIL', error=str(exc)[:2000])
        # Leave business admission stopped after partial bootstrap, preserving queue/data.
        if lab and lab.stack('micro-test-local-business'):
            try:
                publisher_schedule(lab, False); pipe(lab, 'STOPPED')
                result['triggers'] = 'STOPPED'
            except Exception as stop_error:
                result['stopError'] = str(stop_error)[:500]
        raise
    finally:
        save(report, result)
        print('Report: ' + str(report), flush=True)


@contextlib.contextmanager
def exclusive_bootstrap():
    # Outside generated directories so cleanup cannot unlink an active lock.
    path = ROOT / '.lifecycle.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Another bootstrap/cleanup is running in this checkout')
        yield


def verify():
    for directory in ('scripts', 'business/tests', 'scaling', 'observability', 'tests'):
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / directory), '-p', 'test_*.py'], check=True)
    from lab import validate
    from business import validate as validate_business
    validate(); validate_business()
    subprocess.run([str(Path(sys.executable).with_name('cfn-lint')), str(ROOT / 'scaling/network.yaml')], check=True)
    print('PASS: offline verification; fresh WSL runtime is a separate acceptance gate')


def main():
    if sys.argv[1:2] == ['cleanup']:
        import cleanup
        with exclusive_bootstrap(): cleanup.main(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['up', 'status', 'http', 'test', 'pause', 'resume', 'verify'])
    parser.add_argument('--endpoint', type=endpoint, default=ENDPOINT)
    parser.add_argument('--client-endpoint', type=endpoint, default=ENDPOINT)
    args = parser.parse_args()
    if args.command == 'verify':
        verify(); return
    if args.command == 'up':
        if args.endpoint != ENDPOINT:
            parser.error('up requires WSL loopback; remote bootstrap is forbidden')
        with exclusive_bootstrap(): bootstrap(args)
        return
    # Existing lab can be observed and exercised; no emulator lifecycle changes.
    command = {'pause': 'stop', 'resume': 'start'}.get(args.command, args.command)
    subprocess.run([sys.executable, str(ROOT / 'business/business.py'), command, '--endpoint', args.endpoint], check=True)


if __name__ == '__main__':
    main()
