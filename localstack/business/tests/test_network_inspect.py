"""Ensure network diagnostics never print unrelated containers or secret environment."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from network_inspect import summarize


class NetworkInspectTests(unittest.TestCase):
    def test_runtime_keeps_network_but_excludes_credentials(self):
        container = {"Config": {"Env": ["AWS_LAMBDA_FUNCTION_NAME=micro-test-local-publisher",
                                        "AWS_SECRET_ACCESS_KEY=secret-value", "LAB_AWS_ENDPOINT=http://172.19.0.5:4566"]},
                     "NetworkSettings": {"Networks": {"bridge": {"IPAddress": "172.17.0.3", "NetworkID": "id"}}}}
        result = summarize(container)
        self.assertEqual(result["networks"]["bridge"]["ipv4"], "172.17.0.3")
        self.assertEqual(result["configuration"], {"LAB_AWS_ENDPOINT": "http://172.19.0.5:4566"})
        self.assertNotIn("secret-value", str(result))

    def test_localstack_never_reports_license(self):
        result = summarize({"Config": {"Image": "localstack/localstack-pro:latest", "Env": [
            "LOCALSTACK_AUTH_TOKEN=license-value", "PERSISTENCE=0", "LAMBDA_DOCKER_NETWORK=bridge"]}})
        self.assertEqual(result["configuration"], {"PERSISTENCE": "0", "LAMBDA_DOCKER_NETWORK": "bridge"})
        self.assertNotIn("license-value", str(result))

    def test_unrelated_container_excluded(self):
        self.assertIsNone(summarize({"Config": {"Image": "postgres:16", "Env": ["PASSWORD=secret"]}}))


if __name__ == "__main__":
    unittest.main()
