"""Project actual sequential SFN history into explicitly reconstructed OTLP telemetry.

No request bodies, JWTs, signed URLs or raw exception causes leave this module.
An unclosed state is reported in logs, never exported as a successful finished span.
"""
from datetime import datetime
import hashlib
import json
import re
import uuid

EXEC_PREFIX = 'arn:aws:states:us-east-1:000000000000:execution:micro-test-local-purchases:'
STATES = {'Normalize', 'Claim', 'Claimed', 'Document', 'Policy', 'Upload', 'CompletePolicy',
          'Release', 'Finish', 'RecordFailure', 'Failed', 'Duplicate', 'Completed',
          'ValidateBatch', 'ParseEvent', 'ValidateEvent', 'InvalidEvent'}
TERMINAL = {'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED'}


def ident(value, length=16):
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def ns(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return int(value.timestamp() * 1_000_000) * 1000


def context(execution):
    arn = execution['executionArn']
    if not arn.startswith(EXEC_PREFIX):
        raise ValueError('Execution outside owned purchase workflow')
    attrs = {'executionArn': arn, 'telemetry.source': 'localstack.stepfunctions.history',
             'telemetry.reconstructed': True, 'workflow.status': execution['status']}
    sent = None
    try:
        records = json.loads(execution['input'])
        if not isinstance(records, list) or len(records) != 1:
            raise ValueError('Not one SQS record')
        record = records[0]
        body = json.loads(record['body'])
        for key in ('saleId', 'eventId', 'correlationId'):
            if key in body:
                attrs[key] = str(uuid.UUID(body[key]))
        if type(body.get('generation')) is int:
            attrs['generation'] = body['generation']
        stamp = record.get('attributes', {}).get('SentTimestamp')
        if stamp:
            sent = int(stamp) * 1_000_000
    except (ValueError, TypeError, KeyError, IndexError):
        # Malformed contract remains visible as a failed workflow without exposing its payload.
        attrs['input.contract'] = 'unrecognized'
    return attrs, sent


def error_name(event):
    for key, value in event.items():
        if key.endswith('EventDetails') and isinstance(value, dict):
            error = value.get('error')
            if isinstance(error, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', error):
                return error
    return event['type']


def project(execution, events):
    attrs, sent = context(execution)
    arn = execution['executionArn']
    trace = ident(arn, 32)
    root = ident(arn + ':purchase')
    workflow = ident(arn + ':workflow')
    spans, logs = [], []
    current, attempt = None, None
    last_step = None
    by_id = {e['id']: e for e in events}
    start = ns(execution['startDate'])

    def span(key, service, name, begin, end, parent, extra=None, error=None):
        if end < begin:
            raise ValueError('History timestamp order is invalid')
        spans.append({'key': key, 'trace': trace, 'id': ident(arn + ':' + key), 'parent': parent,
                      'service': service, 'name': name, 'start': begin, 'end': end,
                      'attrs': attrs | (extra or {}), 'error': error})

    def log(event, message, extra=None, error=False):
        logs.append({'key': str(event['id']), 'trace': trace, 'span': workflow,
                     'service': 'purchase-workflow', 'time': ns(event['timestamp']),
                     'message': message, 'error': error, 'attrs': attrs | (extra or {})})

    for event in sorted(by_id.values(), key=lambda x: x['id']):
        kind = event['type']
        stamp = ns(event['timestamp'])
        if kind.endswith('StateEntered'):
            name = event.get('stateEnteredEventDetails', {}).get('name', '')
            if name not in STATES:
                raise ValueError('Unexpected state; this observer supports the owned sequential purchase ASL')
            if current:
                raise ValueError('Overlapping states require a parallel-aware observer')
            current = {'name': name, 'id': event['id'], 'start': stamp, 'errors': [], 'attempts': 0, 'lastError': None}
            last_step = name
            log(event, 'Entró en ' + name, {'workflow.step': name, 'workflow.phase': 'entered'})
        elif kind == 'TaskScheduled' and current:
            details = event.get('taskScheduledEventDetails', {})
            current['transport'] = ('http-nlb' if details.get('resourceType') == 'http'
                                    and details.get('resource') == 'invoke' else 'service-integration')
        elif kind in ('LambdaFunctionStarted', 'TaskStarted') and current:
            current['attempts'] += 1
            attempt = {'id': event['id'], 'start': stamp, 'number': current['attempts'],
                       'transport': 'lambda-transport' if kind == 'LambdaFunctionStarted' else current.get('transport', 'service-integration')}
        elif kind in ('LambdaFunctionSucceeded', 'TaskSucceeded', 'LambdaFunctionFailed', 'TaskFailed',
                      'LambdaFunctionTimedOut', 'TaskTimedOut', 'LambdaFunctionScheduleFailed',
                      'LambdaFunctionStartFailed', 'TaskStartFailed', 'TaskSubmitFailed') and current:
            failure = not kind.endswith('Succeeded')
            error = error_name(event) if failure else None
            current['lastError'] = error
            if failure:
                current['errors'].append(error)
                log(event, current['name'] + ': ' + error,
                    {'workflow.step': current['name'], 'error.type': error, 'workflow.phase': 'attempt_failed'}, True)
            if attempt:
                span('attempt:' + str(attempt['id']), attempt['transport'],
                     current['name'] + ' / intento ' + str(attempt['number']), attempt['start'], stamp,
                     ident(arn + ':state:' + str(current['id'])),
                     {'workflow.step': current['name'], 'attempt': attempt['number'],
                      'measurement': 'Task duration from SFN history; reconstructed, not native HTTP/SQL instrumentation'}, error)
                attempt = None
            elif failure:
                # Scheduling failed before a Lambda started: a log, not an invented invocation.
                pass
        elif kind.endswith('StateExited') and current:
            name = event.get('stateExitedEventDetails', {}).get('name')
            if name != current['name']:
                raise ValueError('State exit does not match entry')
            span('state:' + str(current['id']), 'purchase-workflow', name, current['start'], stamp, workflow,
                 {'workflow.step': name, 'attempts': current['attempts'], 'failedAttempts': len(current['errors'])},
                 current['lastError'])
            log(event, 'Salió de ' + name, {'workflow.step': name, 'workflow.phase': 'exited'})
            current, attempt = None, None
        elif kind in ('ExecutionSucceeded', 'ExecutionFailed', 'ExecutionTimedOut', 'ExecutionAborted'):
            log(event, 'Workflow ' + execution['status'], {'workflow.phase': 'terminal'}, kind != 'ExecutionSucceeded')

    if sent is not None and 0 < sent <= start:
        span('queue', 'sqs-pipe', 'SQS → inicio de Step Functions', sent, start, root,
             {'measurement': 'SQS SentTimestamp to execution start; combined wait/dispatch interval'})
    if execution['status'] in TERMINAL:
        end = ns(execution['stopDate'])
        failure = execution['status'] != 'SUCCEEDED'
        if current:
            span('state:' + str(current['id']), 'purchase-workflow', current['name'], current['start'], end,
                 workflow, {'workflow.step': current['name'], 'closure': 'execution terminated without StateExited'},
                 execution['status'] if failure else 'MissingStateExit')
        span('workflow', 'purchase-workflow', 'Step Functions ' + execution['status'], start, end, root,
             error=execution['status'] if failure else None)
        span('purchase', 'purchase-workflow', 'Compra ' + attrs.get('saleId', attrs.get('eventId', 'evento inválido')),
             min(sent, start) if sent and 0 < sent <= start else start, end, '',
             {'coverage': 'SQS dispatch and workflow only; API/SQL/PDF HTTP spans are not instrumented'},
             execution['status'] if failure else None)
    summary = {'executionArn': arn, 'traceId': trace, 'saleId': attrs.get('saleId'),
               'eventId': attrs.get('eventId'), 'status': execution['status'],
               'currentStep': current['name'] if current else None, 'lastStep': last_step}
    return spans, logs, summary
