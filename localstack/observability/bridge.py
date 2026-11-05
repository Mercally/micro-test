"""Read-only LocalStack history -> OTLP; allowlisted fields, resumable export ledger."""
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'business'))
from business import Lab, OPENER, endpoint, outputs
from model import TERMINAL, project
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest, ExportTraceServiceResponse
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest, ExportLogsServiceResponse


def attributes(target, values):
    for key, value in values.items():
        if value is None:
            continue
        item = target.add(key=key)
        if type(value) is bool:
            item.value.bool_value = value
        elif type(value) is int:
            item.value.int_value = value
        else:
            item.value.string_value = str(value)


def payload(kind, records):
    message = ExportTraceServiceRequest() if kind == 'traces' else ExportLogsServiceRequest()
    groups = defaultdict(list)
    for record in records:
        groups[record['service']].append(record)
    for service, rows in groups.items():
        resources = message.resource_spans.add() if kind == 'traces' else message.resource_logs.add()
        attributes(resources.resource.attributes, {'service.name': service, 'service.namespace': 'micro-test-local',
                   'telemetry.source': 'LocalStack read-only observation', 'telemetry.reconstructed': True})
        scope = resources.scope_spans.add() if kind == 'traces' else resources.scope_logs.add()
        scope.scope.name = 'micro-test-local.observer'
        scope.scope.version = '1'
        for record in rows:
            if kind == 'traces':
                item = scope.spans.add(trace_id=bytes.fromhex(record['trace']), span_id=bytes.fromhex(record['id']),
                    parent_span_id=bytes.fromhex(record['parent']), name=record['name'], kind=1,
                    start_time_unix_nano=record['start'], end_time_unix_nano=record['end'], flags=1)
                # UNSET for a completed state with no observed error, ERROR otherwise.
                if record.get('error'):
                    item.status.code = 2
                    item.status.message = record['error']
            else:
                item = scope.log_records.add(time_unix_nano=record['time'], observed_time_unix_nano=time.time_ns(),
                    severity_number=17 if record['error'] else 9, severity_text='ERROR' if record['error'] else 'INFO',
                    trace_id=bytes.fromhex(record.get('trace', '')), span_id=bytes.fromhex(record.get('span', '')), flags=1)
                item.body.string_value = record['message']
            attributes(item.attributes, record['attrs'])
    return message


class Sink:
    def __init__(self, key, port=4318):
        if port != 4318:
            raise ValueError('This observer exports only to its loopback dashboard on port 4318')
        self.key = key

    def send(self, kind, records):
        if not records:
            return
        # Fixed local destination: no remote collector, proxy or redirect fallback.
        request = urllib.request.Request('http://127.0.0.1:4318/v1/' + kind,
            payload(kind, records).SerializeToString(),
            {'Content-Type': 'application/x-protobuf', 'x-otlp-api-key': self.key}, method='POST')
        with OPENER.open(request, timeout=10) as response:
            data = response.read()
        result = ExportTraceServiceResponse() if kind == 'traces' else ExportLogsServiceResponse()
        result.ParseFromString(data)
        partial = result.partial_success
        rejected = partial.rejected_spans if kind == 'traces' else partial.rejected_log_records
        if rejected or partial.error_message:
            raise RuntimeError('Dashboard rejected telemetry: ' + str(rejected))


class Bridge:
    def __init__(self, gateway, state, sink):
        self.lab = Lab(endpoint(gateway))
        self.states = self.lab.client('stepfunctions')
        self.logs = self.lab.client('logs')
        self.machine = outputs(self.lab)['StateMachineArn']
        self.state = Path(state)
        self.sink = sink
        self.db = sqlite3.connect(self.state / 'export.sqlite')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS exported (key TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS completed (arn TEXT PRIMARY KEY);
        ''')
        self.running = set()
        self.summary = {}
        self.cloudwatch_start = int((time.time() - 24 * 3600) * 1000)

    def emit(self, kind, records, namespace):
        pending = [r for r in records if not self.db.execute('SELECT 1 FROM exported WHERE key=?',
                    (namespace + ':' + kind + ':' + r['key'],)).fetchone()]
        for offset in range(0, len(pending), 100):
            batch = pending[offset:offset + 100]
            self.sink.send(kind, batch)
            # Commit only after a successful OTLP response; failures are retried next poll.
            self.db.executemany('INSERT OR IGNORE INTO exported VALUES (?)',
                               [(namespace + ':' + kind + ':' + r['key'],) for r in batch])
            self.db.commit()

    def history(self, arn):
        events, token, tokens = [], None, set()
        while True:
            params = {'executionArn': arn, 'maxResults': 1000}
            if token:
                params['nextToken'] = token
            page = self.states.get_execution_history(**params)
            events.extend(page['events'])
            token = page.get('nextToken')
            if not token:
                return events
            if token in tokens:
                raise RuntimeError('Repeated SFN pagination token')
            tokens.add(token)

    def cloudwatch(self):
        # Import only existing structured publisher/transport messages. Do not forward
        # arbitrary Lambda stdout, customer payloads, exception causes or signed URLs.
        for suffix in ('publisher', 'transport'):
            params = {'logGroupName': '/aws/lambda/micro-test-local-' + suffix,
                      'startTime': self.cloudwatch_start, 'limit': 1000}
            tokens = set()
            while True:
                page = self.logs.filter_log_events(**params)
                rows = []
                for event in page.get('events', []):
                    try:
                        value = json.loads(event['message'])
                        event_id = str(uuid.UUID(value['eventId']))
                        sale_id = str(uuid.UUID(value.get('saleId') or value['correlationId']))
                    except (ValueError, KeyError, TypeError):
                        continue
                    if suffix == 'publisher' and value.get('event') == 'outbox_published':
                        label = 'Outbox publicado en SQS'
                    elif suffix == 'transport' and value.get('ok') is True and value.get('operation') in (
                            'claim', 'document', 'policy', 'upload', 'completePolicy', 'release', 'finish', 'fail'):
                        label = 'Micro respondió a ' + value['operation']
                    else:
                        continue
                    # Logs lack execution ARN. Do not invent a single parent when an event
                    # has multiple executions; correlate by saleId/eventId attributes instead.
                    rows.append({'key': event['eventId'], 'service': 'lambda-' + suffix,
                                 'time': event['timestamp'] * 1_000_000, 'message': label, 'error': False,
                                 'attrs': {'saleId': sale_id, 'correlationId': sale_id, 'eventId': event_id,
                                           'telemetry.source': 'cloudwatch.structured-log'}})
                self.emit('logs', rows, suffix)
                token = page.get('nextToken')
                if not token or token in tokens:
                    break
                tokens.add(token)
                params['nextToken'] = token

    def tick(self):
        # Bounded historical backfill + all executions already observed while running.
        recent = self.states.list_executions(stateMachineArn=self.machine, maxResults=100)['executions'][:100]
        arns = {x['executionArn'] for x in recent} | self.running
        for arn in sorted(arns):
            if self.db.execute('SELECT 1 FROM completed WHERE arn=?', (arn,)).fetchone():
                continue
            execution = self.states.describe_execution(executionArn=arn)
            events = self.history(arn)
            endings = {'ExecutionSucceeded': 'SUCCEEDED', 'ExecutionFailed': 'FAILED',
                       'ExecutionTimedOut': 'TIMED_OUT', 'ExecutionAborted': 'ABORTED'}
            terminal = next((e for e in reversed(events) if e['type'] in endings), None)
            if terminal:
                # History can advance between DescribeExecution and this read.
                execution = execution | {'status': endings[terminal['type']], 'stopDate': terminal['timestamp']}
            elif execution['status'] in TERMINAL:
                raise RuntimeError('Terminal execution history is not available yet; retry next poll')
            spans, logs, summary = project(execution, events)
            self.emit('traces', spans, arn)
            self.emit('logs', logs, arn)
            self.summary[arn] = summary
            if execution['status'] in TERMINAL:
                self.db.execute('INSERT OR IGNORE INTO completed VALUES (?)', (arn,))
                self.db.commit()
                self.running.discard(arn)
            else:
                self.running.add(arn)
        self.cloudwatch()
        # Overlap covers late-arriving logs; exported IDs prevent duplicate ingestion.
        self.cloudwatch_start = int((time.time() - 5 * 60) * 1000)
        return {'observedExecutions': self.db.execute('SELECT COUNT(*) FROM completed').fetchone()[0],
                'active': [self.summary[a] for a in sorted(self.running)],
                'exportedRecords': self.db.execute('SELECT COUNT(*) FROM exported').fetchone()[0]}
