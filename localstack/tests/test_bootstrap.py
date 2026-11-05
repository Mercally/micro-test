"""Fresh-run safety, required stages and failure behavior; no Docker/AWS calls."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('local_bootstrap', ROOT / 'run.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
import business
import http_workflow
import yaml


def emulator():
    image = yaml.safe_load((ROOT / 'compose.yaml').read_text())['services']['localstack']['image']
    return {'Id': 'owned', 'Name': '/' + runner.NAME, 'State': {'Running': True},
            'Config': {'Image': image, 'Labels': {'lab.launcher': runner.OWNER,
                        'com.docker.compose.project': 'micro-test-local'},
                       'Env': ['PERSISTENCE=1', 'LOCALSTACK_AUTH_TOKEN=DO_NOT_REPORT']},
            'HostConfig': {'PortBindings': {'4566/tcp': [{'HostPort': '4566'}]}},
            'Mounts': [{'Type': 'volume', 'Name': 'micro-test-local_state', 'Destination': '/var/lib/localstack'}]}


class BootstrapTests(unittest.TestCase):
    def test_existing_laptop_emulator_refused_before_mutation(self):
        old = emulator(); old['Name'] = '/localstack-aws'
        with patch.object(runner, 'docker_containers', return_value=[old]), patch.object(runner, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'preserved'):
                runner.ensure_emulator()
            command.assert_not_called()

    def test_owned_name_is_not_enough(self):
        for mutate in (lambda c: c['Config'].update(Labels={}),
                       lambda c: c['Config'].update(Env=['PERSISTENCE=0']),
                       lambda c: c.update(Mounts=[]),
                       lambda c: c['Config'].update(Image='localstack/localstack-pro:latest'),
                       lambda c: c['HostConfig'].update(PortBindings={})):
            value = emulator(); mutate(value)
            with self.assertRaisesRegex(ValueError, 'preserve'):
                runner.owned_emulator([value])

    def test_foreign_port_container_and_second_emulator_refused(self):
        for image, bindings in [('nginx', {'80/tcp': [{'HostPort': '4566'}]}),
                                ('localstack/localstack:other', {})]:
            value = emulator(); value['Name'] = '/foreign'; value['Config']['Image'] = image
            value['HostConfig']['PortBindings'] = bindings
            with self.assertRaises(ValueError): runner.owned_emulator([value, emulator()])

    def test_owned_emulator_reused(self):
        self.assertEqual(runner.owned_emulator([emulator()])['Id'], 'owned')

    def test_compose_pins_image_and_persistence_without_public_nlb(self):
        body = yaml.safe_load((ROOT / 'compose.yaml').read_text())
        config = body['services']['localstack']
        self.assertIn('@sha256:', config['image'])
        self.assertEqual(config['environment']['PERSISTENCE'], '1')
        self.assertEqual(config['ports'], ['127.0.0.1:4566:4566'])
        self.assertIn('state:/var/lib/localstack', config['volumes'])
        self.assertNotIn('restart', config)

    def test_new_template_has_no_transport_or_legacy_rule(self):
        body = business.template('business')
        resources = body['Resources']
        self.assertEqual({v['Properties']['Handler'] for v in resources.values()
                          if v['Type'] == 'AWS::Lambda::Function'}, {'publisher.handler', 'authorizer.handler'})
        self.assertNotIn('AWS::Events::Rule', {v['Type'] for v in resources.values()})
        asl = http_workflow.definition('http://local:4512', 'connection')
        self.assertEqual({v['Resource'] for v in asl['States'].values() if v['Type'] == 'Task'},
                         {'arn:aws:states:::http:invoke'})
        self.assertNotIn('lambda:InvokeFunction', json.dumps(resources['WorkflowRole']))
        self.assertEqual(body['Metadata']['WorkflowTransport'], 'http-nlb')
        self.assertEqual(asl['States']['Normalize']['Parameters']['owner.$'], '$.owner')
        self.assertEqual(asl['States']['ParseEvent']['Parameters']['owner.$'], '$$.Execution.Id')

    def pipeline(self, failed_node=False):
        lab = Mock(); lab.stack.return_value = {'StackStatus': 'CREATE_COMPLETE'}
        kube = Mock(); kube.cluster = 'owned-k3d'; kube.report_dir = ROOT / '.state/test-bootstrap'
        flow, scaling, karpenter = Mock(), Mock(), Mock()
        flow.deploy.return_value = {'status': 'CONFIGURED'}
        scaling.sync_targets.return_value = {'status': 'PASS'}
        scaling.install_hpa.return_value = {'status': 'APPLIED'}
        karpenter.install.return_value = {'status': 'INSTALLED'}
        karpenter.node_test.return_value = {'status': 'FAIL' if failed_node else 'PASS', 'httpResponded': not failed_node}
        recorded = []
        with ExitStack() as stack:
            for name, value in [('require_wsl', None), ('docker_containers', []),
                                ('ensure_emulator', emulator()), ('pause_and_drain', None),
                                ('scaling_modules', (flow, scaling, karpenter)),
                                ('cluster_runtime', (kube, 'k3d-owned', 'micro-test-local-eks')),
                                ('report_dir', ROOT / '.state/test-bootstrap'), ('Lab', lab),
                                ('run', Mock(stdout=json.dumps([emulator()])) )]:
                stack.enter_context(patch.object(runner, name, return_value=value))
            stack.enter_context(patch.object(runner, 'save', side_effect=lambda p,v: recorded.append((str(p),copy.deepcopy(v)))))
            for target in ('tools.install', 'lab.deploy', 'kube.bootstrap', 'kube.registry', 'lambda_network.prepare', 'business.deploy'):
                stack.enter_context(patch(target))
            stack.enter_context(patch('business.invoke', return_value={'schemaReady': True}))
            smoke = stack.enter_context(patch('business.smoke'))
            http = stack.enter_context(patch('business.http_file', return_value={'file': 'purchase.http'}))
            pipe = stack.enter_context(patch('business.pipe'))
            schedule = stack.enter_context(patch('business.publisher_schedule'))
            with patch('builtins.print'):
                if failed_node:
                    with self.assertRaisesRegex(RuntimeError, 'node-test failed'):
                        runner.bootstrap(argparse.Namespace(client_endpoint=runner.ENDPOINT))
                else:
                    runner.bootstrap(argparse.Namespace(client_endpoint=runner.ENDPOINT))
            return recorded, smoke, http, pipe, schedule

    def test_all_stages_include_real_node_test_before_purchase_acceptance(self):
        records, smoke, http, _, _ = self.pipeline()
        final = records[-1][1]
        self.assertEqual(tuple(final['stages']), runner.STAGES)
        self.assertEqual(final['status'], 'PASS')
        smoke.assert_called_once(); http.assert_called_once()
        self.assertNotIn('DO_NOT_REPORT', json.dumps(records))
        self.assertIn('NOT_TESTED', final['persistenceRecovery'])

    def test_failed_node_never_marks_full_lab_pass_or_starts_business(self):
        records, smoke, http, pipe, schedule = self.pipeline(failed_node=True)
        final = records[-1][1]
        self.assertEqual(final['status'], 'FAIL')
        self.assertEqual(final['currentStage'], 'node-test')
        self.assertNotIn('node-test', final['stages'])
        smoke.assert_not_called(); http.assert_not_called()
        self.assertTrue(all(c.args[1] == 'STOPPED' for c in pipe.call_args_list))
        self.assertTrue(all(c.args[1] is False for c in schedule.call_args_list))
        self.assertTrue(any(p.endswith('/node-test.json') and v['status'] == 'FAIL' for p,v in records))

    def test_status_does_not_use_bootstrap_or_docker(self):
        with patch.object(sys, 'argv', ['run.py', 'status', '--endpoint', 'http://100.100.154.68:4566']), \
                patch.object(runner, 'bootstrap') as boot, patch.object(runner, 'run') as docker, \
                patch.object(runner.subprocess, 'run') as child:
            runner.main()
            boot.assert_not_called(); docker.assert_not_called()
            self.assertIn('status', child.call_args.args[0])


if __name__ == '__main__': unittest.main()
