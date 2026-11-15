"""A restored CloudFormation stack must not hide a broken live runtime."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import business


class StatusTests(unittest.TestCase):
    def live_lab(self):
        lab = Mock()
        lab.outputs.return_value = {'ClusterName': 'micro-test-local-eks'}
        client = lab.client.return_value
        client.describe_cluster.return_value = {'cluster': {'status': 'ACTIVE'}}
        client.list_nodegroups.return_value = {'nodegroups': ['micro-test-local-' + role for role in ('general', 'onprem', 'terceros')]}
        client.describe_nodegroup.return_value = {'nodegroup': {'status': 'ACTIVE'}}
        client.describe_db_instances.return_value = {'DBInstances': [{'DBInstanceStatus': 'available'}]}
        return lab

    def test_live_probe_requires_application_specific_not_found_response(self):
        lab = self.live_lab()
        with patch.object(business, 'invoke', return_value={'schemaReady': True}) as invoke, \
                patch.object(business, 'token', return_value='fixture'), \
                patch.object(business, 'http_request', return_value=(404, {'error': 'SALE_NOT_FOUND'})) as request:
            self.assertEqual(business.runtime_health(lab)['status'], 'PASS')
            invoke.assert_called_once_with(lab, 'publisher', {'health': True})
            self.assertEqual(len(request.call_args.args), 2)  # GET, no purchase mutation
            for response in ((500, {'message': None}), (401, {}), (404, {'message': 'Not found'})):
                request.return_value = response
                with self.assertRaisesRegex(RuntimeError, 'application probe failed'):
                    business.runtime_health(lab)

    def test_failed_workers_database_or_schema_prevent_ready_status(self):
        lab = self.live_lab()
        client = lab.client.return_value
        with patch.object(business, 'invoke', return_value={'schemaReady': False}), \
                patch.object(business, 'http_request') as request:
            client.describe_nodegroup.return_value = {'nodegroup': {'status': 'CREATE_FAILED'}}
            with self.assertRaisesRegex(RuntimeError, 'node groups'): business.runtime_health(lab)
            client.describe_nodegroup.return_value = {'nodegroup': {'status': 'ACTIVE'}}
            client.describe_db_instances.return_value = {'DBInstances': [{'DBInstanceStatus': 'failed'}]}
            with self.assertRaisesRegex(RuntimeError, 'RDS'): business.runtime_health(lab)
            client.describe_db_instances.return_value = {'DBInstances': [{'DBInstanceStatus': 'available'}]}
            with self.assertRaisesRegex(RuntimeError, 'schema'): business.runtime_health(lab)
            request.assert_not_called()

    def test_completed_stacks_with_failed_eks_cannot_report_pass(self):
        lab = Mock()
        lab.info = {}; lab.health = {'features': {}}
        lab.stack.return_value = {'StackStatus': 'CREATE_COMPLETE'}
        lab.outputs.side_effect = lambda suffix: {
            'eks': {'ClusterName': 'micro-test-local-eks'},
            'database': {}, 'business': {}}[suffix]
        eks = Mock()
        eks.describe_cluster.return_value = {'cluster': {'status': 'FAILED'}}
        lab.client.return_value = eks
        reports = []
        with patch.object(sys, 'argv', ['business.py', 'status', '--endpoint', 'http://127.0.0.1:4566']), \
                patch.object(business, 'Lab', return_value=lab), \
                patch.object(business, 'report_dir', return_value=Path('/tmp/status-fixture')), \
                patch.object(business, 'outputs', return_value={}), \
                patch.object(business, 'pipe', return_value={'CurrentState': 'RUNNING'}), \
                patch.object(business, 'publisher_schedule', return_value={'State': 'ENABLED'}), \
                patch.object(business, 'save', side_effect=lambda p,r: reports.append(r)), \
                patch('builtins.print'):
            with self.assertRaises(RuntimeError): business.main()
        self.assertEqual(reports[-1]['status'], 'FAIL')
        self.assertFalse(reports[-1]['readyForTesting'])


if __name__ == '__main__': unittest.main()
