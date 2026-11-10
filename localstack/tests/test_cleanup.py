"""Cleanup safety gates and partial-operation recovery; no live Docker/AWS calls."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('local_cleanup', ROOT / 'cleanup.py')
cleanup = importlib.util.module_from_spec(spec); spec.loader.exec_module(cleanup)


def container(char, name, image='busybox', labels=None, env=(), cmd=(), mounts=()):
    return {'Id': char * 64, 'Name': '/' + name, 'Image': 'sha256:' + char * 64, 'Created': 'original',
            'Config': {'Image': image, 'Labels': labels or {}, 'Env': list(env), 'Cmd': list(cmd)},
            'HostConfig': {'PortBindings': {}}, 'Mounts': list(mounts), 'State': {'Running': True}}


def fixture():
    emulator = container('a', 'localstack-aws', 'localstack/localstack-pro:2026.10.4',
                         env=['LOCALSTACK_AUTH_TOKEN=SECRET', 'PERSISTENCE=0'],
                         mounts=[{'Type': 'volume', 'Name': 'lab-data', 'Destination': '/var/lib/localstack'}])
    emulator['HostConfig']['PortBindings'] = {'4566/tcp': [{'HostPort': '4566'}]}
    node = container('b', 'k3d-micro-test-local-abc-agent-0', labels={'k3d.cluster': 'micro-test-local-abc', 'k3d.role': 'agent'},
                     cmd=['--kubelet-arg=provider-id=aws:///us-east-1a/i-owned'])
    fn = 'micro-test-local-publisher'
    lam = container('c', 'localstack-aws-lambda-' + fn + '-executor', env=['AWS_LAMBDA_FUNCTION_NAME=' + fn])
    foreign = container('d', 'other-project')
    network = {'Id': 'e' * 64, 'Name': 'k3d-micro-test-local-abc', 'Created': 'original', 'Driver': 'bridge',
               'Labels': {}, 'Containers': {c['Id']: {} for c in (emulator, node)}}
    volume = {'Name': 'lab-data', 'CreatedAt': 'original', 'Driver': 'local', 'Labels': {}, 'Options': {}}
    data = {'engine': 'engine-one', 'containers': [emulator, node, lam, foreign],
            'volumes': [volume], 'networks': [network],
            'images': [{'Id': emulator['Image'], 'RepoTags': ['localstack/localstack-pro:2026.10.4']},
                       {'Id': 'sha256:lab', 'RepoTags': ['docs-simulator:local']}]}
    facts = {'stacks': ['micro-test-local-business'], 'functions': [fn],
             'clusters': ['micro-test-local-eks'], 'instances': ['i-owned']}
    return data, facts


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.data, self.facts = fixture()
        self.plan = cleanup.make_plan(self.data, 'localstack-aws', self.facts)

    def test_runtime_only_cleanup_preserves_venv_and_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'localstack'
            for name in cleanup.FILE_ROOTS:
                (root / name).mkdir(parents=True)
                (root / name / 'retained-or-discarded.txt').write_text('fixture')
            with patch.object(cleanup, 'ROOT', root), patch.object(cleanup.subprocess, 'check_output', return_value=b''):
                cleanup.clean_files(apply=True, roots=('.state',))
                with self.assertRaises(ValueError): cleanup.clean_files(apply=True, roots=('../',))
            self.assertFalse((root / '.state').exists())
            self.assertTrue((root / '.venv/retained-or-discarded.txt').exists())
            self.assertTrue((root / 'reports/retained-or-discarded.txt').exists())

    def test_inventory_is_scoped_and_redacted(self):
        self.assertEqual({c['id'] for c in self.plan['containers']}, {c * 64 for c in 'abc'})
        self.assertEqual(self.plan['unselectedContainers'][0]['name'], 'other-project')
        self.assertNotIn('SECRET', json.dumps(self.plan))
        self.assertNotIn('Env', json.dumps(self.plan))
        self.assertEqual(self.plan['images'], [{'id': 'sha256:lab', 'tags': ['docs-simulator:local']}])

    def test_similar_name_without_binding_is_not_owned(self):
        self.data['containers'][1]['Config']['Cmd'] = ['--kubelet-arg=provider-id=aws:///a/i-foreign']
        self.data['containers'][2]['Config']['Env'] = []
        plan = cleanup.make_plan(self.data, 'localstack-aws', self.facts)
        self.assertEqual([c['id'] for c in plan['containers']], ['a' * 64])
        self.assertFalse(plan['networks'])

    def test_k3d_tools_and_ec2_provider_worker_are_selected_by_owned_cluster_and_instance(self):
        self.data['containers'].extend([
            container('1', 'k3d-micro-test-local-abc-tools', 'ghcr.io/k3d-io/k3d-tools:5.9.0',
                      labels={'k3d.cluster': 'micro-test-local-abc', 'k3d.role': 'noRole'}),
            container('2', 'localstack-ec2.i-owned'),
            container('3', 'localstack-ec2.i-foreign')])
        self.data['networks'].append({'Id': '4' * 64, 'Name': 'localstack-ec2-link-local',
                                     'Containers': {'2' * 64: {}}})
        plan = cleanup.make_plan(self.data, 'localstack-aws', self.facts)
        self.assertEqual({c['id'] for c in plan['containers']}, {c * 64 for c in 'abc12'})
        self.assertEqual({n['id'] for n in plan['networks']}, {c * 64 for c in 'e4'})

    def test_unknown_k3d_helper_and_shared_link_local_network_are_preserved(self):
        self.data['containers'].append(container('1', 'unexpected', 'busybox',
            labels={'k3d.cluster': 'micro-test-local-abc', 'k3d.role': 'noRole'}))
        self.data['networks'].append({'Id': '4' * 64, 'Name': 'localstack-ec2-link-local',
                                     'Containers': {'a' * 64: {}, 'd' * 64: {}}})
        plan = cleanup.make_plan(self.data, 'localstack-aws', self.facts)
        self.assertNotIn('b' * 64, {c['id'] for c in plan['containers']})
        self.assertNotIn('4' * 64, {n['id'] for n in plan['networks']})

    def test_shared_resources_retained_even_with_explicit_selection(self):
        self.data['containers'][3]['Mounts'] = copy.deepcopy(self.data['containers'][0]['Mounts'])
        self.data['networks'][0]['Containers']['d' * 64] = {}
        plan = cleanup.make_plan(self.data, 'localstack-aws', self.facts,
                                 extra_volumes=['lab-data'], extra_networks=['e' * 64])
        self.assertFalse(plan['volumes']); self.assertFalse(plan['networks'])
        self.assertTrue(any(r['kind'] == 'volume' for r in plan['retained']))

    def test_manual_helper_requires_exact_observed_id(self):
        with self.assertRaises(ValueError): cleanup.make_plan(self.data, 'localstack-aws', self.facts, ['dddd'])
        plan = cleanup.make_plan(self.data, 'localstack-aws', self.facts, ['d' * 64])
        self.assertEqual(len(plan['containers']), 4)
        with self.assertRaises(ValueError):
            cleanup.make_plan(self.data, 'localstack-aws', self.facts, extra_networks=['bridge'])

    def test_multiple_emulators_and_wrong_port_refused(self):
        self.data['containers'].append(container('f', 'other-ls', 'localstack/localstack:latest'))
        with self.assertRaisesRegex(ValueError, 'Multiple'): cleanup.make_plan(self.data, 'localstack-aws', self.facts)
        self.data['containers'].pop()
        self.data['containers'][0]['HostConfig']['PortBindings'] = {'80/tcp': [{'HostPort': '4566'}]}
        with self.assertRaises(ValueError): cleanup.make_plan(self.data, 'localstack-aws', self.facts)

    def test_changed_engine_container_volume_and_foreign_attachment_block_apply(self):
        mutations = [lambda d: d.update(engine='different'),
                     lambda d: d['containers'][0].update(Created='replacement'),
                     lambda d: d['volumes'][0].update(CreatedAt='replacement'),
                     lambda d: d['networks'][0]['Containers'].update({'foreign': {}}),
                     lambda d: d['containers'][3].update(Mounts=copy.deepcopy(d['containers'][0]['Mounts']))]
        for mutate in mutations:
            data = copy.deepcopy(self.data); mutate(data)
            with self.subTest(mutate=mutate), patch.object(cleanup, 'inventory', return_value=data), patch.object(cleanup, 'run') as cmd:
                with self.assertRaises(ValueError): cleanup.apply_plan(self.plan, 'purge', True)
                cmd.assert_not_called()

    def test_new_lambda_requires_new_plan(self):
        new = copy.deepcopy(self.data['containers'][2]); new['Id'] = 'f' * 64; new['Name'] += '-new'
        self.data['containers'].append(new)
        with self.assertRaisesRegex(ValueError, 'New lab containers'): cleanup.check_plan(self.plan, self.data)

    def test_mount_order_does_not_change_container_identity(self):
        self.data['containers'][0]['Mounts'].append({
            'Type': 'bind', 'Source': '/var/run/docker.sock', 'Destination': '/var/run/docker.sock', 'RW': True})
        plan = cleanup.make_plan(self.data, 'localstack-aws', self.facts)
        self.data['containers'][0]['Mounts'].reverse()
        cleanup.check_plan(plan, self.data)

    def test_mount_content_changes_still_block_before_docker_mutations(self):
        for key, value in [('Name', 'another-volume'), ('Destination', '/another/path'), ('RW', False)]:
            data = copy.deepcopy(self.data)
            data['containers'][0]['Mounts'][0][key] = value
            with self.subTest(key=key), patch.object(cleanup, 'inventory', return_value=data), patch.object(cleanup, 'run') as command:
                with self.assertRaisesRegex(ValueError, 'identity/configuration changed'):
                    cleanup.apply_plan(self.plan, 'purge', True)
                command.assert_not_called()

    def test_old_plan_requires_regeneration_before_mutations(self):
        self.plan['schema'] = 1
        with patch.object(cleanup, 'inventory', return_value=self.data), patch.object(cleanup, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'regenerate with cleanup plan'):
                cleanup.apply_plan(self.plan, 'purge', True)
            command.assert_not_called()

    def test_data_loss_acknowledgement_precedes_any_docker_operation(self):
        for mode in ('purge', 'stop'):
            with patch.object(cleanup, 'inventory') as inv:
                with self.assertRaisesRegex(ValueError, '--delete-data'): cleanup.apply_plan(self.plan, mode)
                inv.assert_not_called()

    def test_admission_and_active_workflows_block_shutdown(self):
        lab = Mock()
        lab.outputs.return_value = {'StateMachineArn': 'arn:aws:states:us-east-1:000000000000:stateMachine:lab'}
        lab.client.return_value.get_template.return_value = {'TemplateBody': {'Resources': {}}}
        lab.client.return_value.list_executions.return_value = {'executions': []}
        for pipe_state, scheduler, executions in [('RUNNING', 'DISABLED', []),
                                                 ('STOPPED', 'ENABLED', []),
                                                 ('STOPPED', 'DISABLED', [{'status': 'RUNNING'}])]:
            lab.client.return_value.list_executions.return_value = {'executions': executions}
            with patch.object(cleanup, 'pipe', return_value={'CurrentState': pipe_state}), \
                    patch.object(cleanup, 'publisher_schedule', return_value={'State': scheduler}):
                with self.assertRaises(ValueError): cleanup.require_idle(lab)
        lab.client.return_value.get_template.return_value = {'TemplateBody': {'Resources': {'Schedule': {}}}}
        lab.client.return_value.describe_rule.return_value = {'State': 'ENABLED'}
        with self.assertRaisesRegex(ValueError, 'Legacy'): cleanup.require_idle(lab)

    def test_foreign_cloudformation_stack_refuses_whole_emulator(self):
        lab = Mock()
        lab.client.return_value.get_paginator.return_value.paginate.return_value = [
            {'Stacks': [{'StackName': 'another-project', 'Tags': []}]}]
        with self.assertRaisesRegex(ValueError, 'foreign CloudFormation'): cleanup.cloud_facts(lab)

    def test_localhost_only_cleanup_rejects_mac(self):
        with patch.object(cleanup.sys, 'platform', 'darwin'):
            with self.assertRaisesRegex(ValueError, 'WSL'): cleanup.require_wsl()

    def run_purge(self, remove_images=False):
        calls = []
        def command(cmd):
            args = cmd[len(cleanup.DOCKER):]; calls.append(args)
            if args[0] == 'stop':
                for c in self.data['containers']:
                    if c['Id'] == args[-1]: c['State']['Running'] = False
                # Helper auto-removes when node stops. Its old ID must not be stopped/removed.
                if args[-1] == 'b' * 64:
                    self.data['containers'] = [c for c in self.data['containers'] if c['Id'] != 'c' * 64]
            elif args[:2] == ['container', 'rm']:
                self.data['containers'] = [c for c in self.data['containers'] if c['Id'] != args[-1]]
                for n in self.data['networks']: n['Containers'].pop(args[-1], None)
            elif args[:2] == ['network', 'rm']: self.data['networks'] = []
            elif args[:2] == ['volume', 'rm']: self.data['volumes'] = []
            elif args[:2] == ['image', 'rm']:
                self.data['images'] = [i for i in self.data['images'] if args[-1] not in i['RepoTags']]
            else: self.fail('Unexpected Docker action: ' + str(args))
            return Mock(stdout='')
        with patch.object(cleanup, 'inventory', side_effect=lambda: copy.deepcopy(self.data)), \
                patch.object(cleanup, 'run', side_effect=command), patch.object(cleanup, 'Lab'), \
                patch.object(cleanup, 'require_idle'):
            result = cleanup.apply_plan(self.plan, 'purge', True, remove_images)
        return result, calls

    def test_purge_handles_auto_removed_helpers_preserves_foreign_and_is_repeatable(self):
        result, calls = self.run_purge(True)
        self.assertEqual(result['status'], 'SELECTED_SCOPE_COMPLETE')
        self.assertEqual([c['name'] for c in result['remainingContainers']], ['other-project'])
        stops = [c[-1] for c in calls if c[0] == 'stop']
        self.assertEqual(stops, ['b' * 64, 'a' * 64])
        self.assertTrue(any(c[:2] == ['image', 'rm'] for c in calls))
        self.assertNotIn('prune', [word for c in calls for word in c])
        self.assertTrue(all('-f' not in c and '-v' not in c for c in calls))
        self.assertEqual(self.run_purge()[1], [])

    def test_stop_preserves_containers_and_storage(self):
        data = copy.deepcopy(self.data)
        for c in data['containers']: c['State']['Running'] = False
        with patch.object(cleanup, 'inventory', return_value=data), patch.object(cleanup, 'run') as command:
            self.assertEqual(cleanup.apply_plan(self.plan, 'stop', True)['status'], 'SELECTED_SCOPE_COMPLETE')
            command.assert_not_called()

    def test_files_require_removed_resources_and_no_recreated_emulator(self):
        with self.assertRaisesRegex(ValueError, 'Purge'): cleanup.files_ready(self.plan, self.data)
        self.run_purge()
        cleanup.files_ready(self.plan, self.data)
        self.data['containers'].append(container('f', 'new', 'localstack/localstack-pro:2026.10.4'))
        with self.assertRaisesRegex(ValueError, 'emulator'): cleanup.files_ready(self.plan, self.data)

    def test_file_cleanup_does_not_follow_symlinks_or_delete_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve(); root = base / 'localstack'; root.mkdir()
            (root / '.state').mkdir(); (root / '.state' / 'generated').write_text('test')
            (root / 'source.py').write_text('keep')
            (root / '.lifecycle.lock').touch()
            with patch.object(cleanup, 'ROOT', root), patch.object(cleanup.subprocess, 'check_output', return_value=b'localstack/source.py\0'):
                cleanup.clean_files(False); self.assertTrue((root / '.state').exists())
                cleanup.clean_files(True)
                self.assertTrue((root / 'source.py').exists()); self.assertTrue((root / '.lifecycle.lock').exists())
                (root / '.state').symlink_to(base, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, 'symlink'): cleanup.clean_files(True)
            (root / '.state').unlink(); (root / '.state').mkdir()
            with patch.object(cleanup, 'ROOT', root), patch.object(cleanup.subprocess, 'check_output', return_value=b'localstack/.state/tracked\0'):
                with self.assertRaisesRegex(ValueError, 'tracked'): cleanup.clean_files(True)

    def test_live_observer_blocks_generated_file_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'localstack'
            (root / '.state/observability').mkdir(parents=True)
            (root / '.state/observability/process.json').write_text('{"pid": 1}')
            (root / 'observability').mkdir()
            (root / 'observability/observe.py').write_text('def own_process(pid): return True\n')
            with patch.object(cleanup, 'ROOT', root), patch.object(cleanup.subprocess, 'check_output', return_value=b''):
                with self.assertRaisesRegex(ValueError, 'observer'): cleanup.clean_files(True)
                self.assertTrue((root / '.state').exists())

    def test_report_outside_checkout_and_symlink_guard(self):
        with self.assertRaises(ValueError): cleanup.report_path(ROOT / '.state/../plan.json')
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            cleanup.write(base / 'plan.json', self.plan)
            self.assertEqual((base / 'plan.json').stat().st_mode & 0o777, 0o600)
            (base / 'link.json').symlink_to(base / 'plan.json')
            with self.assertRaises(ValueError): cleanup.write(base / 'link.json', {})

    def test_dry_run_checks_inventory_but_never_mutates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / 'plan.json'; cleanup.write(path, self.plan)
            with patch.object(cleanup, 'require_wsl'), patch.object(cleanup, 'inventory', return_value=self.data), \
                    patch.object(cleanup, 'apply_plan') as apply, patch('builtins.print'):
                cleanup.main(['purge', '--plan', str(path)])
                apply.assert_not_called()


if __name__ == '__main__': unittest.main()
