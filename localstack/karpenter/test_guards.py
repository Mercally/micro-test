"""Negative checks: never accept remote credentials or metadata as runtime proof."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml
import karpenter as lab


class Guards(unittest.TestCase):
    def config(self, server="https://127.0.0.1:12345", user=None, **cluster_flags):
        return {"clusters": [{"cluster": {"server": server, **cluster_flags}}],
                "users": [{"user": user or {"client-certificate-data": "local", "client-key-data": "local"}}]}

    def check(self, config, internal=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump(config))
            lab.check_config(path, internal)

    def test_external_or_insecure_kubernetes_rejected(self):
        for config in (self.config("https://example.eks.amazonaws.com"),
                       self.config("http://127.0.0.1:12345"),
                       self.config(**{"insecure-skip-tls-verify": True}),
                       self.config("https://100.95.224.37:6443")):
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.check(config)

    def test_exec_credentials_rejected(self):
        with self.assertRaises(ValueError):
            self.check(self.config(user={"exec": {"command": "aws", "args": ["eks", "get-token"]}}))

    def test_loopback_and_observed_internal_endpoints(self):
        self.check(self.config())
        self.check(self.config("https://172.18.0.2:6443"), internal=True)
        with self.assertRaises(ValueError):
            self.check(self.config("https://172.18.0.2:6443"))

    def test_missing_pods_cannot_pass_proof(self):
        with patch.object(lab, "get", return_value={"items": []}), self.assertRaises(ValueError):
            lab.proof()

    def test_two_ready_pods_on_same_node_cannot_pass(self):
        pod = {"spec": {"nodeName": "same"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        with patch.object(lab, "get", return_value={"items": [pod, pod]}), self.assertRaises(ValueError):
            lab.proof()

    def test_worker_deletion_requires_explicit_flag(self):
        with patch.object(lab, "kubectl") as kube, self.assertRaises(ValueError):
            lab.scale_down(False)
        kube.assert_not_called()

    def test_worker_with_other_workload_is_preserved(self):
        claim = {"status": {"nodeName": "worker"}}
        pods = '{"items":[{"metadata":{"namespace":"important"},"spec":{"nodeName":"worker"}}]}'
        with patch.object(lab, "get", return_value={"items": [claim]}), \
                patch.object(lab, "kubectl", return_value=pods) as kube, self.assertRaises(ValueError):
            lab.scale_down(True)
        self.assertEqual(kube.call_count, 1)
        self.assertEqual(kube.call_args.args[:3], (True, "get", "pods"))


if __name__ == "__main__":
    unittest.main()
