#!/usr/bin/env python3
"""Switch the owned purchase workflow and authenticated API to native NLB routing."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'business'))
from business import Lab, endpoint, pipe, deploy_stack, report_dir, save, STATE, template
from http_workflow import configure, definition, route, health


def normalized_template(value):
    value = copy.deepcopy(value)
    keys = [k for k, v in value['Resources'].items() if v['Type'] == 'AWS::ApiGateway::Deployment']
    if len(keys) != 1:
        raise ValueError('Expected one API deployment snapshot')
    deployment = value['Resources'].pop(keys[0])
    value['Resources']['Deployment'] = deployment
    value['Resources']['Stage']['Properties']['DeploymentId'] = {'Ref': 'Deployment'}
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', type=endpoint, default='http://100.100.154.68:4566')
    args = parser.parse_args()
    lab = Lab(args.endpoint)
    report = report_dir()
    result = {'status': 'PREPARING'}
    try:
        stack = lab.stack('micro-test-local-business')
        if not stack or stack['StackStatus'] not in ('CREATE_COMPLETE', 'UPDATE_COMPLETE'):
            raise ValueError('Existing healthy owned business stack required')
        cf = lab.client('cloudformation')
        old = cf.get_template(StackName=stack['StackName'])['TemplateBody']
        if isinstance(old, str): old = yaml.safe_load(old)
        config = route(lab)
        parameters = {p['ParameterKey']: p['ParameterValue'] for p in stack.get('Parameters', [])}
        receipt = STATE / 'business-deployed.json'
        if not receipt.exists() or json.loads(receipt.read_text())['template'] != old:
            # Deployment may have been executed in WSL, with no receipt on Mac.
            # Only accept exact source equivalence, ignoring generated snapshot ID.
            expected = template('business')
            if old.get('Metadata', {}).get('WorkflowTransport') == 'http-nlb':
                expected = configure(expected, config)
            if normalized_template(old) != normalized_template(expected):
                raise ValueError('Missing/stale receipt and live template differs from reviewed source; preserve changes')
            save(receipt, {'endpoint': lab.endpoint, 'template': old, 'parameters': parameters})
            result['receipt'] = 'Verified source-equivalent template; synchronized local receipt'
        result['health'] = health(lab)
        validation = lab.client('stepfunctions').validate_state_machine_definition(
            definition=json.dumps(definition(config['endpoint'], config['connectionArn'])), type='STANDARD')
        if validation['result'] != 'OK':
            raise ValueError('Native workflow definition invalid: ' + json.dumps(validation))
        body = configure(copy.deepcopy(old), config)
        deployments = [k for k, v in body['Resources'].items() if v['Type'] == 'AWS::ApiGateway::Deployment']
        if len(deployments) != 1:
            raise ValueError('Expected one API deployment')
        deployment = body['Resources'].pop(deployments[0])
        body['Resources']['Stage']['Properties']['DeploymentId'] = {'Ref': 'REPLACED_AFTER_HASH'}
        name = 'DeploymentHttp' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
        body['Resources'][name] = deployment
        body['Resources']['Stage']['Properties']['DeploymentId'] = {'Ref': name}
        save(report / 'before.json', {'template': old, 'parameters': parameters})
        save(report / 'after.json', {'template': body, 'parameters': parameters})
        cf.validate_template(TemplateBody=json.dumps(body))
        state = pipe(lab)['CurrentState']
        if state not in ('RUNNING', 'STOPPED'):
            raise ValueError('Pipe is transitioning; retry after it settles')
        pipe(lab, 'STOPPED')
        print('Pipe stopped; existing workflows finish before switching transport', flush=True)
        states = lab.client('stepfunctions')
        arn = lab.outputs('business')['StateMachineArn']
        deadline = time.monotonic() + 270
        while states.list_executions(stateMachineArn=arn, statusFilter='RUNNING', maxResults=1)['executions']:
            if time.monotonic() >= deadline:
                if state == 'RUNNING': pipe(lab, 'RUNNING')
                raise TimeoutError('Workflows still running; no configuration updated')
            time.sleep(3)
        # Failure during update leaves admission stopped and queued messages intact.
        deploy_stack(lab, 'business', body, parameters, update=True)
        deployed = json.loads(states.describe_state_machine(stateMachineArn=arn)['definition'])
        tasks = [s for s in deployed['States'].values() if s['Type'] == 'Task']
        if not tasks or any(s['Resource'] != 'arn:aws:states:::http:invoke' for s in tasks):
            raise ValueError('Deployed workflow still contains non-HTTP tasks')
        if state == 'RUNNING': pipe(lab, 'RUNNING')
        result.update(status='CONFIGURED', endpoint=config['endpoint'], httpTasks=len(tasks),
                      api='Authenticated routes now target same NLB', pipe=state,
                      purchaseAcceptance='PENDING: run business.py test; health is not purchase proof')
    except Exception as exc:
        result.update(status='FAIL', error=str(exc)[:2000])
        raise
    finally:
        save(report / 'http-cutover.json', result)
        print(json.dumps(result, indent=2))
        print('Report: ' + str(report))


if __name__ == '__main__':
    main()
