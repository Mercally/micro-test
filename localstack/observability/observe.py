#!/usr/bin/env python3
"""Run a loopback Aspire dashboard and read-only laptop observer, without Docker/AppHost."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'business'))
from business import OPENER, endpoint

STATE = HERE.parent / '.state/observability'
UI = 'http://127.0.0.1:18888'
SCRIPT = str(Path(__file__).resolve())


def write(name, value):
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = STATE / name
    if path.is_symlink():
        raise ValueError('Observer state cannot be a symlink')
    temp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
    temp.replace(path)


def read(name):
    return json.loads((STATE / name).read_text())


def own_process(pid):
    result = subprocess.run(['ps', '-p', str(int(pid)), '-o', 'args='], text=True, capture_output=True)
    return result.returncode == 0 and SCRIPT + ' serve ' in result.stdout


def api(path, config):
    if not path.startswith('/api/telemetry/'):
        raise ValueError('Only dashboard telemetry API reads supported')
    request = urllib.request.Request(UI + path, headers={'x-api-key': config['Dashboard']['Api']['PrimaryApiKey']})
    with OPENER.open(request, timeout=5) as response:
        return json.load(response)


def serve(gateway):
    from bridge import Bridge, Sink
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    lock = open(STATE / 'run.lock', 'a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('An observer is already running') from None
    # Do not claim or stop unrelated servers on these ports.
    for port in (18888, 4317, 4318):
        with socket.socket() as sock:
            # Allow recent closed connections (TIME_WAIT), never an active listener.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', port))
    executable = shutil.which('aspire')
    if not executable:
        raise RuntimeError('Aspire CLI 13.6 required; see localstack/observability/README.md')
    version = subprocess.check_output([executable, '--version'], text=True).strip()
    if not version.startswith('13.6.'):
        raise RuntimeError('Validated with Aspire CLI 13.6.x; found ' + version)
    config_path = STATE / 'dashboard.json'
    if config_path.exists():
        config = read('dashboard.json')
    else:
        config = {'Dashboard': {
            'Frontend': {'AuthMode': 'Unsecured'},
            'Otlp': {'AuthMode': 'ApiKey', 'PrimaryApiKey': secrets.token_urlsafe(32)},
            'Api': {'AuthMode': 'ApiKey', 'PrimaryApiKey': secrets.token_urlsafe(32)},
            'Data': {'Directory': str(STATE / 'dashboard-data'), 'PersistenceMode': 'Resume'},
            'TelemetryLimits': {'MaxTraceCount': 5000, 'MaxLogCount': 50000}}}
        write('dashboard.json', config)
    # The dashboard has no secrets in its command line and listens only on loopback.
    command = [executable, 'dashboard', 'run', '--frontend-url', UI,
               '--otlp-grpc-url', 'http://127.0.0.1:4317', '--otlp-http-url', 'http://127.0.0.1:4318',
               '--application-name', 'micro-test-local-observer', '--persistence', 'Resume',
               '--config-file-path', str(config_path), '--non-interactive', '--nologo']
    env = {k: v for k, v in os.environ.items() if not k.startswith(('AWS_', 'DASHBOARD_', 'ASPIRE_DASHBOARD_', 'OTEL_'))}
    env.pop('LOCALSTACK_AUTH_TOKEN', None)
    env['DOTNET_CLI_TELEMETRY_OPTOUT'] = '1'
    env['ASPIRE_CLI_TELEMETRY_OPTOUT'] = '1'
    fd = os.open(STATE / 'dashboard.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    bridge = None
    with os.fdopen(fd, 'w') as log:
        process = subprocess.Popen(command, cwd=STATE, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            write('process.json', {'pid': os.getpid(), 'endpoint': gateway, 'ui': UI, 'aspireVersion': version})
            deadline = time.monotonic() + 180
            while not stop.is_set():
                if process.poll() is not None:
                    raise RuntimeError('Aspire exited; inspect localstack/.state/observability/dashboard.log')
                try:
                    api('/api/telemetry/resources', config)
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Aspire startup timeout; inspect dashboard.log')
                    stop.wait(1)
            if stop.is_set():
                return
            bridge = Bridge(gateway, STATE, Sink(config['Dashboard']['Otlp']['PrimaryApiKey']))
            print('Dashboard: ' + UI, flush=True)
            while not stop.is_set():
                if process.poll() is not None:
                    raise RuntimeError('Aspire stopped unexpectedly')
                try:
                    status = bridge.tick()
                    write('status.json', status | {'status': 'RUNNING', 'endpoint': gateway,
                          'ui': UI, 'lastSuccessfulPoll': time.time(), 'source': 'reconstructed SFN history / structured logs'})
                except Exception as exc:
                    # Never emit raw API exception bodies or credentials into reports.
                    write('status.json', {'status': 'DEGRADED', 'endpoint': gateway, 'ui': UI,
                          'errorType': type(exc).__name__, 'message': str(exc)[:250] if isinstance(exc, ValueError) else 'Read/export failed; next poll retries',
                          'observedAt': time.time()})
                    print('Observer poll failed: ' + type(exc).__name__, flush=True)
                stop.wait(3)
        finally:
            if bridge:
                bridge.db.close()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            write('status.json', {'status': 'STOPPED', 'ui': UI, 'endpoint': gateway})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'serve', 'status', 'stop', 'traces'])
    parser.add_argument('--endpoint', type=endpoint, default='http://100.100.154.68:4566')
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.command == 'serve':
        return serve(args.endpoint)
    info = read('process.json') if (STATE / 'process.json').exists() else {}
    running = bool(info and own_process(info['pid']))
    if args.command == 'status':
        result = read('status.json') if (STATE / 'status.json').exists() else {'status': 'NOT_STARTED'}
        print(json.dumps(result | {'supervisorAlive': running}, indent=2))
    elif args.command == 'stop':
        if running:
            os.kill(info['pid'], signal.SIGTERM)
            print('Stopping only this observer and its dashboard; laboratory remains running.')
        else:
            print('Observer is not running; no process stopped.')
    elif args.command == 'traces':
        if not running:
            raise RuntimeError('Start observer first')
        print(json.dumps(api('/api/telemetry/traces', read('dashboard.json')), indent=2))
    elif running:
        if info['endpoint'] != args.endpoint:
            raise ValueError('Observer uses another endpoint; stop it before changing source')
        print('Already running: ' + UI)
    else:
        # Separate process + session keep UI available after the command returns.
        fd = os.open(STATE / 'observer.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'w') as log:
            child = subprocess.Popen([sys.executable, SCRIPT, 'serve', '--endpoint', args.endpoint],
                        cwd=HERE.parent.parent, stdin=subprocess.DEVNULL, stdout=log,
                        stderr=subprocess.STDOUT, start_new_session=True)
        print('Observer launching, PID ' + str(child.pid) + '; UI ' + UI)
        print('Check: python localstack/observability/observe.py status')


if __name__ == '__main__':
    main()
