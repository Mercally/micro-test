#!/usr/bin/env python3
"""Local-only network feasibility and Pipes/scaling observations. Never cuts over purchases."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'business'))
from business import Lab, OPENER, endpoint, outputs, report_dir, save, deploy_stack, pipe


def inventory(lab):
    result = {'localstack': lab.info, 'features': lab.health.get('features'),
              'pipe': pipe(lab), 'nlbs': lab.client('elbv2').describe_load_balancers()['LoadBalancers']}
    result['pipe'].pop('ResponseMetadata', None)
    for label, service, method, params in [
        ('lattice', 'vpc-lattice', 'list_resource_configurations', {}),
        ('nodeAMI', 'ssm', 'get_parameter', {'Name': '/aws/service/eks/optimized-ami/1.34/amazon-linux-2023/x86_64/standard/recommended/image_id'})]:
        try:
            response = getattr(lab.client(service), method)(**params)
            response.pop('ResponseMetadata', None)
            result[label] = {'status': 'API_AVAILABLE', 'result': response}
        except Exception as exc:
            result[label] = {'status': 'UNAVAILABLE', 'error': str(exc)[:500]}
    return result


def deploy(lab):
    body = yaml.safe_load((HERE / 'network.yaml').read_text())
    # LocalStack allocates an external-service port for TCP listeners. Discover it
    # after creation; :4566 only routes management API / ALB HTTP requests.
    exists = lab.stack('micro-test-local-scaling-network')
    port = tcp_listener(lab)['Port'] if exists else 80
    deploy_stack(lab, 'scaling-network', body, {'NlbRuntimePort': port}, update=bool(exists))
    observed = tcp_listener(lab)['Port']
    if observed != port:
        deploy_stack(lab, 'scaling-network', body, {'NlbRuntimePort': observed}, update=True)
    return {'status': 'CONFIGURED', 'listenerPort': observed,
            'scope': 'Control plane only; target routing and HTTP execution require probe',
            'outputs': lab.outputs('scaling-network')}


def target_group(lab):
    out = lab.outputs('scaling-network')
    tags = lab.client('elbv2').describe_tags(ResourceArns=[out['TargetGroupArn']])['TagDescriptions'][0]['Tags']
    if {'Key': 'Lab', 'Value': 'micro-test-local'} not in tags:
        raise ValueError('Target group ownership mismatch')
    return out


def select_tcp_listener(load_balancer, listeners, target_arn):
    if load_balancer.get('Type') != 'network' or load_balancer.get('Scheme') != 'internal':
        raise ValueError('Expected owned internal Network Load Balancer')
    if len(listeners) != 1:
        raise ValueError('Expected exactly one observed NLB listener')
    listener = listeners[0]
    actions = listener.get('DefaultActions', [])
    if (listener.get('Protocol') != 'TCP' or listener.get('LoadBalancerArn') != load_balancer['LoadBalancerArn']
            or type(listener.get('Port')) is not int or not 1 <= listener['Port'] <= 65535
            or listener['Port'] == 4566 or len(actions) != 1 or actions[0].get('Type') != 'forward'
            or actions[0].get('TargetGroupArn') != target_arn):
        raise ValueError('Unexpected NLB listener protocol, port or target')
    return listener


def tcp_listener(lab):
    out = target_group(lab)
    elb = lab.client('elbv2')
    tags = elb.describe_tags(ResourceArns=[out['NlbArn']])['TagDescriptions'][0]['Tags']
    if {'Key': 'Lab', 'Value': 'micro-test-local'} not in tags:
        raise ValueError('NLB ownership mismatch')
    lb = elb.describe_load_balancers(LoadBalancerArns=[out['NlbArn']])['LoadBalancers'][0]
    if lb['DNSName'] != out['NlbDns']:
        raise ValueError('NLB DNS differs from owned stack output')
    return select_tcp_listener(lb, elb.describe_listeners(LoadBalancerArn=out['NlbArn'])['Listeners'], out['TargetGroupArn'])


def probe(lab):
    out = target_group(lab)
    dns = out['NlbDns']
    if not dns.endswith('.elb.localhost.localstack.cloud') or '/' in dns:
        raise ValueError('Unexpected LocalStack NLB DNS')
    listener = tcp_listener(lab)
    runtime_url = f'http://{dns}:{listener["Port"]}/actuator/health'
    definition = json.loads(lab.client('stepfunctions').describe_state_machine(stateMachineArn=out['ProbeArn'])['definition'])
    if definition['States']['Health']['Parameters']['ApiEndpoint'] != runtime_url:
        raise ValueError('HTTP probe does not use observed TCP listener port; run deploy-network first')
    result = {'scope': 'NLB application routing and native HTTP Task; not TLS/Lattice parity',
              'runtimeUrl': runtime_url, 'listenerPort': listener['Port'],
              'targets': lab.client('elbv2').describe_target_health(TargetGroupArn=out['TargetGroupArn'])['TargetHealthDescriptions']}
    client_url = f'http://{urlsplit(lab.endpoint).hostname}:{listener["Port"]}/actuator/health'
    request = urllib.request.Request(client_url, headers={'Host': dns})
    try:
        with OPENER.open(request, timeout=5) as response:
            data = json.load(response)
            result['nlbHttp'] = {'status': 'PASS' if response.status == 200 and data.get('status') == 'UP' else 'FAIL',
                                 'response': data}
    except urllib.error.HTTPError as exc:
        result['nlbHttp'] = {'status': 'FAIL', 'httpStatus': exc.code}
    except Exception as exc:
        result['nlbHttp'] = {'status': 'UNREACHABLE_FROM_CLIENT', 'error': str(exc)[:500],
                             'note': 'Host port publication is separate from in-container reachability; runtime probe decides acceptance'}
    states = lab.client('stepfunctions')
    arn = states.start_execution(stateMachineArn=out['ProbeArn'], name='health-' + uuid.uuid4().hex, input='{}')['executionArn']
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        run = states.describe_execution(executionArn=arn)
        if run['status'] != 'RUNNING':
            break
        time.sleep(1)
    history = states.get_execution_history(executionArn=arn)['events']
    result['nativeHttp'] = {k: run.get(k) for k in ('executionArn', 'status', 'error', 'cause', 'output')}
    result['nativeHttp']['eventTypes'] = [x['type'] for x in history]
    output = json.loads(run.get('output') or '{}')
    body = output.get('ResponseBody', {})
    if isinstance(body, str):
        try: body = json.loads(body)
        except ValueError: body = {}
    result['status'] = ('PASS' if run['status'] == 'SUCCEEDED' and output.get('StatusCode') == 200
                        and isinstance(body, dict) and body.get('status') == 'UP' else 'FAIL')
    return result


def observe(lab, window=60):
    if window <= 0:
        raise ValueError('Observation window must be positive')
    now = datetime.now(timezone.utc)
    out = outputs(lab)
    queue = lab.client('sqs').get_queue_url(QueueName='micro-test-local-purchases')['QueueUrl']
    attrs = lab.client('sqs').get_queue_attributes(QueueUrl=queue, AttributeNames=[
        'ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible', 'ApproximateNumberOfMessagesDelayed'])['Attributes']
    states = lab.client('stepfunctions')
    active = sum(len(page['executions']) for page in states.get_paginator('list_executions').paginate(
        stateMachineArn=out['StateMachineArn'], statusFilter='RUNNING'))
    starts = 0
    # Count actual execution starts; never interpret target invocation metrics as running workflows.
    for page in states.get_paginator('list_executions').paginate(stateMachineArn=out['StateMachineArn']):
        recent = [x for x in page['executions'] if x['startDate'] >= now - timedelta(seconds=window)]
        starts += len(recent)
        if len(recent) != len(page['executions']):
            break
    return {'observedAt': now.isoformat(), 'windowSeconds': window, 'queueApproximate': attrs,
            'startedInWindow': starts, 'startsPerSecondAverage': starts / window, 'activeWorkflows': active,
            'warning': 'Sampling is not atomic. SQS backlog excludes work already admitted to Standard workflows.'}


def configure_pipe(lab, window):
    if not 0 <= window <= 300:
        raise ValueError('Batching window must be between 0 and 300 seconds')
    current = pipe(lab)  # Verifies source, destination, role and ownership.
    params = dict(current.get('SourceParameters', {}))
    params['SqsQueueParameters'] = {'BatchSize': 1, 'MaximumBatchingWindowInSeconds': window}
    lab.client('pipes').update_pipe(Name=current['Name'], RoleArn=current['RoleArn'], SourceParameters=params)
    observed = pipe(lab)
    if observed.get('SourceParameters', {}).get('SqsQueueParameters') != params['SqsQueueParameters']:
        raise RuntimeError('Pipe provider did not preserve requested source parameters')
    return {'status': 'APPLIED', 'sourceParameters': observed['SourceParameters'],
            'note': 'Batch window is an upper wait bound, not a starts/second limiter. BatchSize remains 1.'}


def capacity(rate, cpu_seconds, cpu_request, target, minimum, maximum):
    values = (rate, cpu_seconds, cpu_request, target)
    if not all(math.isfinite(v) for v in values) or rate < 0 or cpu_seconds < 0 or cpu_request <= 0 or not 0 < target <= 1:
        raise ValueError('Finite nonnegative demand, positive CPU request and target in (0,1] required')
    if minimum < 1 or maximum < minimum:
        raise ValueError('Invalid replica bounds')
    demand = rate * cpu_seconds
    needed = math.ceil(demand / (cpu_request * target))
    return {'cpuCoresDemand': demand, 'estimatedReplicas': max(minimum, needed),
            'boundedReplicas': min(maximum, max(minimum, needed)), 'capacityExceeded': needed > maximum,
            'assumption': 'Measured aggregate CPU seconds per purchase in this deployment; steady state. Excludes waits, bursts and non-CPU bottlenecks. Not HPA prediction.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'deploy-network', 'probe', 'observe', 'estimate',
                                          'pipe-pause', 'pipe-resume', 'configure-pipe'])
    parser.add_argument('--endpoint', type=endpoint, default='http://100.100.154.68:4566')
    parser.add_argument('--rate', type=float, default=1)
    parser.add_argument('--cpu-seconds', type=float, default=.2)
    parser.add_argument('--cpu-request', type=float, default=.1)
    parser.add_argument('--target', type=float, default=.6)
    parser.add_argument('--min', type=int, default=1)
    parser.add_argument('--max', type=int, default=4)
    parser.add_argument('--batch-window', type=int, default=0)
    args = parser.parse_args()
    path = report_dir() / (args.command + '.json')
    try:
        if args.command == 'estimate':
            result = capacity(args.rate, args.cpu_seconds, args.cpu_request, args.target, args.min, args.max)
        else:
            lab = Lab(args.endpoint)
            if args.command in ('pipe-pause', 'pipe-resume'):
                current = pipe(lab, 'STOPPED' if args.command == 'pipe-pause' else 'RUNNING')
                result = {'status': current['CurrentState'], 'note': 'Existing workflows continue; publisher unchanged'}
            elif args.command == 'configure-pipe':
                result = configure_pipe(lab, args.batch_window)
            else:
                fn = {'inspect': inventory, 'deploy-network': deploy, 'probe': probe, 'observe': observe}[args.command]
                result = fn(lab)
        save(path, result)
        print(json.dumps(result, default=str, indent=2))
        print('Report: ' + str(path))
        if result.get('status') == 'FAIL':
            raise SystemExit(1)
    except Exception as exc:
        save(path, {'status': 'FAIL', 'error': str(exc)[:1000]})
        print('Report: ' + str(path), file=sys.stderr)
        raise


if __name__ == '__main__':
    main()
