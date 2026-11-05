"""Native HTTP purchase workflow through the observed LocalStack NLB TCP listener."""
import copy
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODE = 'http-nlb'


def flow_module():
    spec = importlib.util.spec_from_file_location('nlb_flow', HERE.parent / 'scaling/flow.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def route(lab):
    flow = flow_module()
    out = flow.target_group(lab)
    listener = flow.tcp_listener(lab)
    dns = out['NlbDns']
    if dns != 'micro-test-local-nlb.elb.localhost.localstack.cloud':
        raise ValueError('Unexpected lab NLB hostname')
    connection = lab.client('events').describe_connection(Name='micro-test-local-http-probe')
    if connection['ConnectionArn'] != out['ConnectionArn']:
        raise ValueError('Connection differs from owned scaling stack')
    return {'endpoint': f'http://{dns}:{listener["Port"]}', 'connectionArn': out['ConnectionArn'],
            'secretArn': connection['SecretArn'], 'listenerPort': listener['Port']}


def definition(endpoint, connection_arn):
    original = json.loads((HERE / 'workflow.asl.json').read_text())
    states = copy.deepcopy(original['States'])
    del states['Normalize']
    states['ValidateBatch'] = {
        'Type': 'Choice', 'Choices': [{'And': [
            {'Variable': '$[0]', 'IsPresent': True}, {'Variable': '$[1]', 'IsPresent': False},
            {'Variable': '$[0].eventSource', 'IsPresent': True},
            {'Variable': '$[0].eventSource', 'StringEquals': 'aws:sqs'},
            {'Variable': '$[0].body', 'IsPresent': True}, {'Variable': '$[0].body', 'IsString': True}],
            'Next': 'ParseEvent'}], 'Default': 'InvalidEvent'}
    states['ParseEvent'] = {'Type': 'Pass', 'Parameters': {'event.$': 'States.StringToJson($[0].body)',
                           'owner.$': '$$.Execution.Id'}, 'Next': 'ValidateEvent'}
    uuid_pattern = '/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/'
    conditions = ["$type($states.input.event) = 'object'", "$states.input.event.type = 'PurchaseAccepted'",
                  "$type($states.input.event.version) = 'number'", '$states.input.event.version = 1',
                  "$type($states.input.event.generation) = 'number'", '$states.input.event.generation >= 1',
                  '$floor($states.input.event.generation) = $states.input.event.generation']
    for key in ('eventId', 'saleId', 'correlationId'):
        conditions += [f"$type($states.input.event.{key}) = 'string'",
                       f'$exists($match($states.input.event.{key}, {uuid_pattern}))']
    conditions += ['$states.input.event.saleId = $states.input.event.correlationId']
    states['ValidateEvent'] = {'Type': 'Choice', 'QueryLanguage': 'JSONata',
                              'Choices': [{'Condition': '{% ' + ' and '.join(conditions) + ' %}', 'Next': 'Normalize'}],
                              'Default': 'InvalidEvent'}
    states['Normalize'] = {'Type': 'Pass', 'Parameters': {
        **{key + '.$': '$.event.' + key for key in ('eventId', 'saleId', 'correlationId', 'generation')},
        'owner.$': '$.owner'}, 'Next': 'Claim'}
    states['InvalidEvent'] = {'Type': 'Fail', 'Error': 'InvalidEvent', 'Cause': 'Expected one valid PurchaseAccepted SQS event'}
    for name, state in states.items():
        if state['Type'] != 'Task':
            continue
        operation = state['Parameters']['operation']
        body = {'eventId.$': '$.eventId', 'owner.$': '$.owner', 'operation': operation}
        if name in ('Policy', 'Upload', 'CompletePolicy', 'Release', 'Finish'):
            body['documentId.$'] = '$.document.body.documentId'
        if name in ('Upload', 'CompletePolicy', 'Release', 'Finish'):
            body['policyId.$'] = '$.policy.body.policyId'
        if name == 'RecordFailure':
            body['error.$'] = 'States.JsonToString($.failure)'
        state.update(Resource='arn:aws:states:::http:invoke', Parameters={
            'ApiEndpoint.$': "States.Format('" + endpoint + "/api/v1/internal/purchases/{}/run', $.saleId)",
            'Method': 'POST', 'InvocationConfig': {'ConnectionArn': connection_arn},
            'Headers': {'Content-Type': 'application/json', 'X-Correlation-Id.$': '$.correlationId'},
            'RequestBody': body}, ResultSelector={'body.$': '$.ResponseBody'})
        state['Retry'] = [{'ErrorEquals': [
            'States.Http.StatusCode.429', 'States.Http.StatusCode.500', 'States.Http.StatusCode.502',
            'States.Http.StatusCode.503', 'States.Http.StatusCode.504', 'States.Http.StatusCode.408',
            # Task deadline is 20s, below HTTP's 60s socket deadline. Installed
            # LocalStack parser rejects States.Http.Socket in a Retry declaration.
            'States.Timeout'], 'IntervalSeconds': 2, 'MaxAttempts': 3, 'BackoffRate': 2}]
    states['Claimed']['Choices'][0]['Variable'] = '$.claim.body.claimed'
    return {'Comment': 'Native HTTP to local NLB TCP listener; no Lambda tasks. Docker network replaces AWS private connectivity only.',
            'StartAt': 'ValidateBatch', 'TimeoutSeconds': original['TimeoutSeconds'], 'States': states}


def configure(body, config):
    """Mutate only workflow, its IAM policy and authenticated API integration URIs."""
    endpoint, connection = config['endpoint'], config['connectionArn']
    body.setdefault('Metadata', {})['WorkflowTransport'] = MODE
    body['Metadata']['LocalNlbEndpoint'] = endpoint
    resources = body['Resources']
    resources['Workflow']['Properties']['DefinitionString'] = json.dumps(definition(endpoint, connection))
    resources['WorkflowRole']['Properties']['Policies'] = [{
        'PolicyName': 'invoke-owned-nlb-http', 'PolicyDocument': {'Version': '2012-10-17', 'Statement': [
            {'Effect': 'Allow', 'Action': 'states:InvokeHTTPEndpoint',
             'Resource': 'arn:aws:states:us-east-1:000000000000:stateMachine:micro-test-local-purchases',
             'Condition': {'StringEquals': {'states:HTTPMethod': 'POST'},
                           'StringLike': {'states:HTTPEndpoint': endpoint + '/api/v1/internal/purchases/*/run'}}},
            {'Effect': 'Allow', 'Action': 'events:RetrieveConnectionCredentials', 'Resource': connection},
            {'Effect': 'Allow', 'Action': ['secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret'],
             'Resource': config['secretArn']} ]}}]
    for name, path in [('AcceptMethod', '/api/v1/purchases'), ('StatusMethod', '/api/v1/purchases/{saleId}'),
                       ('RetryMethod', '/api/v1/purchases/{saleId}/retry')]:
        method = resources[name]['Properties']
        if method['AuthorizationType'] != 'CUSTOM' or method['Integration']['Type'] != 'HTTP_PROXY':
            raise ValueError('Unexpected business API authentication/integration')
        method['Integration']['Uri'] = endpoint + path
    return body


def live_http(lab):
    value = lab.client('cloudformation').get_template(StackName='micro-test-local-business')['TemplateBody']
    if isinstance(value, str):
        import yaml
        value = yaml.safe_load(value)
    return value.get('Metadata', {}).get('WorkflowTransport') == MODE


def health(lab):
    result = flow_module().probe(lab)
    if result['status'] != 'PASS':
        raise ValueError('NLB native HTTP health failed: ' + json.dumps(result))
    return result
