"""Exercise actual in-pod probe code with simulated sockets, never real networking."""
import contextlib
import copy
import errno
import io
import itertools
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'business'), str(ROOT / 'scripts')]
import karpenter


class ProbeKube:
    def __init__(self, error=None, enforced=True, baseline=True, restore=True, allowed=True, exec_error=False):
        self.error = error or ConnectionRefusedError(errno.ECONNREFUSED, 'refused')
        self.enforced, self.baseline, self.restore, self.allowed = enforced, baseline, restore, allowed
        self.exec_error = exec_error
        self.restricted = False
        self.ever_restricted = False
        self.deleted = False
        self.report_dir = ROOT / '.state/test-network-gate'
        self.pod = None

    def apply_data(self, pod): self.pod = copy.deepcopy(pod)

    def connect(self, *args, **kwargs):
        if not self.baseline or (self.restricted and self.enforced) or (self.ever_restricted and not self.restore):
            raise self.error
        return Mock()

    def call(self, *args, **kwargs):
        if 'delete' in args:
            self.deleted = True
        elif 'label' in args:
            self.restricted = 'local-aws-only=true' in args
            self.ever_restricted = True
        elif 'exec' in args:
            if self.exec_error:
                return SimpleNamespace(returncode=1, stdout='', stderr='exec unavailable')
            code_index = args.index('-c')
            code = args[code_index + 1]
            if 'urllib.request' in code:
                if not self.allowed: raise RuntimeError('allowed endpoint unavailable')
                return SimpleNamespace(returncode=0, stdout='PASS', stderr='')
            stream, result = io.StringIO(), 0
            with contextlib.redirect_stdout(stream), patch('socket.create_connection', side_effect=self.connect), \
                    patch.object(sys, 'argv', ['-c', *args[code_index + 2:]]):
                try: exec(code, {})
                except SystemExit as exc: result = exc.code
                except OSError: result = 1
            response = SimpleNamespace(returncode=result, stdout=stream.getvalue(), stderr='')
            if kwargs.get('check', True) and result: raise RuntimeError('probe failed')
            return response
        return SimpleNamespace(returncode=0, stdout='', stderr='')


class NetworkGateTests(unittest.TestCase):
    def execute(self, kube, succeeds=True):
        reports = []
        with patch.object(karpenter.time, 'sleep'), \
                patch.object(karpenter.time, 'monotonic', side_effect=itertools.count(0, 10)), \
                patch.object(karpenter, 'save', side_effect=lambda path, value: reports.append(copy.deepcopy(value))):
            if succeeds:
                result = karpenter.network_gate(kube, '172.19.0.5', 4511)
            else:
                with self.assertRaises(RuntimeError): karpenter.network_gate(kube, '172.19.0.5', 4511)
                result = None
        self.assertTrue(kube.deleted)
        return result, reports

    def test_immediate_refusal_is_supported_with_connectivity_restored(self):
        result, _ = self.execute(ProbeKube())
        self.assertEqual(result['status'], 'PASS')

    def test_timeout_and_icmp_network_rejections_supported(self):
        for error in (TimeoutError('timeout'), OSError(errno.EHOSTUNREACH, 'host unreachable'),
                      OSError(errno.ENETUNREACH, 'net unreachable'), PermissionError(errno.EACCES, 'denied')):
            with self.subTest(error=error):
                result, reports = self.execute(ProbeKube(error=error))
                self.assertEqual(result['status'], 'PASS')
                self.assertTrue(result['restored'])
                self.assertEqual(reports[-1]['status'], 'PASS')
                self.assertIn('blockedObservation', result)

    def test_unenforced_policy_cannot_pass_and_records_connected_result(self):
        _, reports = self.execute(ProbeKube(enforced=False), succeeds=False)
        self.assertEqual(reports[-1]['status'], 'FAIL')
        self.assertEqual(reports[-1]['phase'], 'restricted')
        self.assertTrue(reports[-1]['lastProbe']['reachable'])

    def test_broken_baseline_or_service_outage_never_counts_as_enforcement(self):
        for kwargs in ({'baseline': False}, {'restore': False}, {'allowed': False}):
            with self.subTest(kwargs=kwargs):
                _, reports = self.execute(ProbeKube(**kwargs), succeeds=False)
                self.assertEqual(reports[-1]['status'], 'FAIL')

    def test_exec_failure_and_unexpected_os_error_never_count_as_network_block(self):
        for kwargs in ({'exec_error': True}, {'error': OSError(errno.EMFILE, 'too many files')}):
            with self.subTest(kwargs=kwargs):
                _, reports = self.execute(ProbeKube(**kwargs), succeeds=False)
                self.assertEqual(reports[-1]['status'], 'FAIL')

    def test_other_existing_probe_is_preserved(self):
        kube = Mock(); kube.call.return_value.stdout = 'pod/egress-proof'
        with self.assertRaises(ValueError): karpenter.network_gate(kube, '172.19.0.5', 4511)
        kube.apply_data.assert_not_called()
        kube.call.assert_called_once()


if __name__ == '__main__': unittest.main()
