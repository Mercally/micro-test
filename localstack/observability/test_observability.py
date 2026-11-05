"""Regression tests for accurate history projection, redaction and OTLP delivery."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from model import EXEC_PREFIX, project
from bridge import Bridge, Sink, payload

DATE = datetime(2026, 11, 5, tzinfo=timezone.utc)
SALE = '11111111-1111-4111-8111-111111111111'
EVENT = '22222222-2222-4222-8222-222222222222'


def execution(status='SUCCEEDED'):
    return {'executionArn': EXEC_PREFIX + 'fixture', 'status': status, 'startDate': DATE,
            'stopDate': DATE + timedelta(seconds=10),
            'input': json.dumps([{'body': json.dumps({'saleId': SALE, 'correlationId': SALE,
                  'eventId': EVENT, 'generation': 1, 'secret': 'DO_NOT_EXPORT'}),
                  'attributes': {'SentTimestamp': str(int(DATE.timestamp() * 1000) - 1000)}}])}


def event(number, kind, step=None, error=None):
    value = {'id': number, 'previousEventId': number - 1, 'type': kind,
             'timestamp': DATE + timedelta(seconds=number)}
    if kind.endswith('StateEntered'):
        value['stateEnteredEventDetails'] = {'name': step, 'input': 'DO_NOT_EXPORT'}
    if kind.endswith('StateExited'):
        value['stateExitedEventDetails'] = {'name': step, 'output': 'SIGNED_URL_DO_NOT_EXPORT'}
    if error:
        value['lambdaFunctionFailedEventDetails'] = {'error': error, 'cause': 'CUSTOMER_DO_NOT_EXPORT'}
    return value


class ProjectionTests(unittest.TestCase):
    def test_native_http_task_not_labelled_lambda(self):
        scheduled = event(4, 'TaskScheduled')
        scheduled['taskScheduledEventDetails'] = {'resourceType': 'http', 'resource': 'invoke', 'parameters': 'DO_NOT_EXPORT'}
        events = [event(1, 'ChoiceStateEntered', 'ValidateBatch'), event(2, 'ChoiceStateExited', 'ValidateBatch'),
                  event(3, 'TaskStateEntered', 'Claim'), scheduled, event(5, 'TaskStarted'),
                  event(6, 'TaskSucceeded'), event(7, 'TaskStateExited', 'Claim'), event(8, 'ExecutionSucceeded')]
        spans, logs, _ = project(execution(), events)
        self.assertEqual(next(s for s in spans if s['key'] == 'attempt:5')['service'], 'http-nlb')
        self.assertNotIn('lambda-transport', {s['service'] for s in spans})
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(spans + logs))

    def test_success_tree_and_redaction(self):
        events = [event(1, 'ExecutionStarted'), event(2, 'TaskStateEntered', 'Document'),
                  event(3, 'LambdaFunctionStarted'), event(4, 'LambdaFunctionSucceeded'),
                  event(5, 'TaskStateExited', 'Document'), event(6, 'ExecutionSucceeded')]
        spans, logs, summary = project(execution(), events)
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(spans + logs))
        self.assertEqual(summary['saleId'], SALE)
        self.assertTrue(all(s['end'] >= s['start'] for s in spans))
        ids = {s['id'] for s in spans}
        self.assertTrue(all(not s['parent'] or s['parent'] in ids for s in spans))
        self.assertEqual(len({s['trace'] for s in spans}), 1)
        protobuf = payload('traces', spans)
        self.assertEqual(sum(len(sc.spans) for r in protobuf.resource_spans for sc in r.scope_spans), len(spans))

    def test_running_state_never_exported_as_completed(self):
        spans, logs, summary = project(execution('RUNNING'), [event(1, 'ExecutionStarted'),
                    event(2, 'TaskStateEntered', 'Upload'), event(3, 'LambdaFunctionStarted')])
        self.assertEqual(summary['currentStep'], 'Upload')
        self.assertEqual([s['key'] for s in spans], ['queue'])
        self.assertIn('Entró en Upload', [r['message'] for r in logs])

    def test_transient_retry_red_but_recovered_state_not_error(self):
        events = [event(1, 'TaskStateEntered', 'Document'), event(2, 'LambdaFunctionStarted'),
                  event(3, 'LambdaFunctionFailed', error='TransientStepError'),
                  event(4, 'LambdaFunctionStarted'), event(5, 'LambdaFunctionSucceeded'),
                  event(6, 'TaskStateExited', 'Document'), event(7, 'ExecutionSucceeded')]
        spans, logs, _ = project(execution(), events)
        state = next(s for s in spans if s['key']=='state:1')
        self.assertIsNone(state['error'])
        self.assertEqual(state['attrs']['failedAttempts'], 1)
        self.assertEqual(len([s for s in spans if s['error']=='TransientStepError']), 1)
        self.assertNotIn('CUSTOMER', json.dumps(spans+logs))

    def test_permanent_catch_state_is_error_and_no_exit_is_explicit(self):
        events = [event(1, 'TaskStateEntered', 'Upload'), event(2, 'LambdaFunctionStarted'),
                  event(3, 'LambdaFunctionFailed', error='PermanentStepError'),
                  event(4, 'TaskStateExited', 'Upload'), event(5, 'FailStateEntered', 'Failed'), event(6, 'ExecutionFailed')]
        spans, _, _ = project(execution('FAILED'), events)
        self.assertEqual(next(s for s in spans if s['key']=='state:1')['error'], 'PermanentStepError')
        self.assertIn('closure', next(s for s in spans if s['key']=='state:5')['attrs'])
        self.assertEqual(next(s for s in spans if s['key']=='purchase')['error'], 'FAILED')

    def test_foreign_arn_and_unknown_states_refused(self):
        with self.assertRaises(ValueError):
            project(execution() | {'executionArn': 'arn:aws:states:us-east-1:999999999999:execution:other:one'}, [])
        with self.assertRaises(ValueError):
            project(execution(), [event(1, 'TaskStateEntered', 'UserDefinedParallel')])

    def test_malformed_input_retains_failed_trace_without_payload(self):
        spans, _, summary = project(execution('FAILED') | {'input':'"DO_NOT_EXPORT"'}, [event(1,'ExecutionFailed')])
        self.assertIsNone(summary['saleId'])
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(spans))


class ExportTests(unittest.TestCase):
    def test_failed_export_retried_and_success_deduplicated(self):
        import sqlite3
        bridge = Bridge.__new__(Bridge)
        bridge.db = sqlite3.connect(':memory:')
        bridge.db.execute('CREATE TABLE exported (key TEXT PRIMARY KEY)')
        bridge.sink = Mock()
        bridge.sink.send.side_effect = [RuntimeError('offline'), None]
        rows = [{'key':'one'}]
        with self.assertRaises(RuntimeError):
            bridge.emit('logs', rows, 'fixture')
        self.assertEqual(bridge.db.execute('SELECT COUNT(*) FROM exported').fetchone()[0], 0)
        bridge.emit('logs', rows, 'fixture')
        bridge.emit('logs', rows, 'fixture')
        self.assertEqual(bridge.sink.send.call_count, 2)
        bridge.db.close()

    def test_sink_rejects_partial_success(self):
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
        response = ExportTraceServiceResponse()
        response.partial_success.rejected_spans = 1
        fake = Mock()
        fake.__enter__ = Mock(return_value=Mock(read=Mock(return_value=response.SerializeToString())))
        fake.__exit__ = Mock(return_value=False)
        with patch('bridge.OPENER.open', return_value=fake):
            with self.assertRaisesRegex(RuntimeError, 'rejected telemetry'):
                Sink('test').send('traces', [{'key':'a','trace':'11'*16,'id':'22'*8,'parent':'','service':'test',
                     'name':'test','start':1,'end':2,'attrs':{}}])

    def test_sink_cannot_target_remote_collector(self):
        with self.assertRaises(ValueError):
            Sink('test', port=4566)


if __name__ == '__main__':
    unittest.main()
