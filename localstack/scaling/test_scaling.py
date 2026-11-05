"""Offline regression checks for scaling limits and local-only controller config."""
import importlib.util
from pathlib import Path
import unittest
import yaml

HERE = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location('scaling_' + name, HERE / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


flow = module('flow')
karpenter = module('karpenter')
runtime = module('runtime')
import http_workflow


class ScalingTests(unittest.TestCase):
    def test_nlb_uses_observed_tcp_listener_not_api_gateway(self):
        lb = {'Type': 'network', 'Scheme': 'internal', 'LoadBalancerArn': 'owned'}
        listener = {'LoadBalancerArn': 'owned', 'Protocol': 'TCP', 'Port': 4512,
                    'DefaultActions': [{'Type': 'forward', 'TargetGroupArn': 'target'}]}
        self.assertEqual(flow.select_tcp_listener(lb, [listener], 'target')['Port'], 4512)
        for change in ({'Port': 4566}, {'Protocol': 'HTTP'}, {'LoadBalancerArn': 'foreign'},
                       {'DefaultActions': [{'Type': 'forward', 'TargetGroupArn': 'foreign'}]}):
            with self.assertRaises(ValueError):
                flow.select_tcp_listener(lb, [listener | change], 'target')

    def test_native_workflow_keeps_owner_and_failure_recovery(self):
        workflow = http_workflow.definition('http://local:4512', 'owned-connection')
        states = workflow['States']
        tasks = [s for s in states.values() if s['Type'] == 'Task']
        self.assertEqual(len(tasks), 8)
        self.assertEqual({s['Resource'] for s in tasks}, {'arn:aws:states:::http:invoke'})
        self.assertTrue(all(s['Parameters']['RequestBody']['owner.$'] == '$.owner' for s in tasks))
        self.assertNotIn('documentId.$', states['RecordFailure']['Parameters']['RequestBody'])
        self.assertEqual(states['Claimed']['Choices'][0]['Variable'], '$.claim.body.claimed')
        self.assertLess(workflow['TimeoutSeconds'], 300)
        self.assertTrue(all('States.Http.StatusCode.422' not in s['Retry'][0]['ErrorEquals'] for s in tasks))

    def test_capacity_saturation_visible_not_hidden_by_cap(self):
        value = flow.capacity(5, .2, .1, .6, 1, 4)
        self.assertEqual(value['estimatedReplicas'], 17)
        self.assertEqual(value['boundedReplicas'], 4)
        self.assertTrue(value['capacityExceeded'])

    def test_idle_and_invalid_inputs(self):
        self.assertEqual(flow.capacity(0, .2, .1, .6, 1, 4)['boundedReplicas'], 1)
        for values in [(1, .2, 0, .6, 1, 4), (float('nan'), .2, .1, .6, 1, 4),
                       (1, .2, .1, 1.5, 1, 4), (1, .2, .1, .6, 4, 1)]:
            with self.assertRaises(ValueError):
                flow.capacity(*values)

    def test_no_public_or_loopback_controller_endpoint(self):
        for address in ['8.8.8.8', '127.0.0.1', '0.0.0.0', 'aws.amazon.com']:
            with self.assertRaises(ValueError):
                karpenter.values(address, 'micro-test-local-eks')
        with self.assertRaises(ValueError):
            karpenter.values('172.19.0.5', 'production')
        value = karpenter.values('172.19.0.5', 'micro-test-local-eks')
        env = {x['name']: x['value'] for x in value['controller']['env']}
        self.assertEqual(env['AWS_ACCESS_KEY_ID'], 'test')
        self.assertTrue(all(v == 'http://172.19.0.5:4566' for k, v in env.items() if k.startswith('AWS_ENDPOINT_URL')))
        self.assertTrue(value['settings']['isolatedVPC'])

    def test_egress_cannot_fall_back_to_public_aws(self):
        api = {'subsets': [{'addresses': [{'ip': '172.19.0.4'}], 'ports': [{'port': 6443}]}]}
        policy = karpenter.egress_policy('172.19.0.5', api)
        rules = policy['spec']['egress']
        self.assertEqual({x['ipBlock']['cidr'] for r in rules for x in r['to'] if 'ipBlock' in x},
                         {'172.19.0.5/32', '172.19.0.4/32'})
        self.assertEqual({p['port'] for r in rules for p in r['ports']}, {4566, 53, 6443})

    def test_practice_pool_cannot_take_business_pods(self):
        _, pool = karpenter.node_resources({'GeneralA': 'subnet-abc', 'ClusterSG': 'sg-abc'}, 'test', '1.34')
        self.assertEqual(pool['spec']['template']['metadata']['labels']['lab-role'], 'karpenter-practice')
        self.assertEqual(pool['spec']['template']['spec']['taints'][0]['effect'], 'NoSchedule')
        self.assertEqual(pool['spec']['disruption']['budgets'], [{'nodes': '0'}])

    def test_hpa_does_not_scale_on_istio_cpu(self):
        spec = yaml.safe_load((HERE / 'hpa.yaml').read_text())['spec']
        self.assertEqual(spec['metrics'][0]['containerResource']['container'], 'micro')
        self.assertEqual(spec['maxReplicas'], 4)
        self.assertEqual(spec['behavior']['scaleDown']['stabilizationWindowSeconds'], 300)

    def test_memory_target_is_independent_of_request_percentage(self):
        resources = {'requests': {'cpu': '100m', 'memory': '384Mi'}, 'limits': {'memory': '768Mi'}}
        spec = runtime.hpa_manifest(resources, 512)['spec']
        metrics = {m['containerResource']['name']: m['containerResource'] for m in spec['metrics']}
        self.assertEqual(metrics['memory']['target'], {'type': 'AverageValue', 'averageValue': '512Mi'})
        self.assertEqual({m['container'] for m in metrics.values()}, {'micro'})
        self.assertEqual(metrics['cpu']['target']['averageUtilization'], 60)

    def test_reject_memory_target_at_or_over_oom_limit(self):
        resources = {'requests': {'cpu': '100m', 'memory': '384Mi'}, 'limits': {'memory': '0.75Gi'}}
        for target in (0, -1, 768, 1024):
            with self.assertRaises(ValueError):
                runtime.hpa_manifest(resources, target)
        with self.assertRaises(ValueError):
            runtime.hpa_manifest({'requests': {'cpu': '100m'}})
        self.assertEqual(runtime.memory_bytes('0.75Gi'), runtime.memory_bytes('768Mi'))


if __name__ == '__main__':
    unittest.main()
