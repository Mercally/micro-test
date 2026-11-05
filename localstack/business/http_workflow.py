"""Native HTTP purchase workflow through the observed LocalStack NLB TCP listener."""
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
    source = (HERE / 'workflow.asl.json').read_text()
    # Endpoint and Connection come from owned LocalStack resources; JSONPath stays intact.
    source = source.replace('${NlbBaseUrl}', endpoint).replace('${ConnectionArn}', connection_arn)
    return json.loads(source)


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
