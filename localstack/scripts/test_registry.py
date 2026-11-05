"""ECR routing regression: preserve local hostname; never log in to AWS."""
import base64
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import kube

HOST = "000000000000.dkr.ecr.us-east-1.localhost.localstack.cloud:4566"
URI = HOST + "/micro-test-local-docs"
DIGEST = "sha256:" + "a" * 64


class RegistryTests(unittest.TestCase):
    def test_domain_preserved_for_both_local_auth_schemes(self):
        for scheme in ("http", "https"):
            self.assertEqual(kube.registry_target(URI, scheme + "://" + HOST), HOST)

    def test_real_aws_or_other_repository_and_auth_redirect_rejected(self):
        pairs = [(URI.replace("localhost.localstack.cloud", "amazonaws.com"), "https://" + HOST),
                 (URI.replace("000000000000", "123456789012"), "https://" + HOST),
                 (URI + "/other", "https://" + HOST),
                 (URI, "https://" + HOST + "@evil.example"),
                 (URI, "https://" + HOST + "/other"),
                 ("127.0.0.1:4566/micro-test-local-docs", "http://127.0.0.1:4566")]
        for uri, proxy in pairs:
            with self.subTest(uri=uri, proxy=proxy), self.assertRaises(ValueError):
                kube.registry_target(uri, proxy)

    def exercise(self, failure=None, address="127.0.0.1", digest=DIGEST):
        lab = Mock(endpoint="http://127.0.0.1:4566")
        ecr = lab.client.return_value
        ecr.describe_repositories.return_value = {"repositories": [{"repositoryUri": URI}]}
        ecr.get_authorization_token.return_value = {"authorizationData": [{"proxyEndpoint": "http://" + HOST,
            "authorizationToken": base64.b64encode(b"AWS:test-secret").decode()}]}
        ecr.describe_images.return_value = {"imageDetails": [{"imageDigest": digest}]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            existing = root / ".state/docker-registry/config.json"
            existing.parent.mkdir(parents=True); existing.write_text("user-config")
            calls = []
            def run(command, **kwargs):
                calls.append((command, kwargs))
                if failure and failure in command:
                    raise RuntimeError("deliberate " + failure + " failure")
                return Mock(returncode=0, stdout="", stderr="")
            with patch.object(sys, "platform", "linux"), \
                    patch.object(Path, "read_text", return_value="microsoft-WSL2"), \
                    patch.object(kube, "ROOT", root), patch.object(kube, "run", side_effect=run), \
                    patch.object(kube.socket, "getaddrinfo", return_value=[(2, 1, 6, "", (address, 4566))]), \
                    patch.object(kube, "save") as save, patch("builtins.print"):
                if failure or address != "127.0.0.1" or digest != DIGEST:
                    with self.assertRaises((ValueError, RuntimeError)):
                        kube.registry(lab)
                    save.assert_not_called()
                else:
                    kube.registry(lab)
                    self.assertEqual(save.call_args.args[1]["transport"], URI)
            self.assertEqual(existing.read_text(), "user-config")
            self.assertEqual(list((root / ".state").glob("docker-registry-*")), [])
        return calls

    def test_login_push_and_digest_pull_keep_host_and_password_stdin(self):
        calls = self.exercise()
        login = next(c for c in calls if "login" in c[0])
        self.assertIn(HOST, login[0]); self.assertEqual(login[1]["input"], "test-secret")
        self.assertNotIn("test-secret", " ".join(login[0]))
        self.assertTrue(any("push" in cmd and URI + ":lab" in cmd for cmd, _ in calls))
        self.assertTrue(any("pull" in cmd and URI + "@" + DIGEST in cmd for cmd, _ in calls))

    def test_push_failure_cleans_only_owned_credentials_and_cannot_pass(self):
        calls = self.exercise(failure="push")
        self.assertTrue(any("logout" in cmd for cmd, _ in calls))
        self.assertFalse(any("pull" in cmd for cmd, _ in calls))

    def test_nonlocal_dns_prevents_docker_login(self):
        self.assertEqual(self.exercise(address="8.8.8.8"), [])

    def test_bad_digest_prevents_pull(self):
        calls = self.exercise(digest="https://external.example/image")
        self.assertFalse(any("pull" in cmd for cmd, _ in calls))

    def test_registry_cli_does_not_rebootstrap_or_construct_kube(self):
        argv = ["kube.py", "--endpoint", "http://127.0.0.1:4566", "--k3d-cluster", "micro-test-local-50dacdf7", "registry"]
        with patch.object(sys, "argv", argv), patch.object(kube, "Lab") as lab, \
                patch.object(kube, "Kube") as constructor, patch.object(kube, "registry") as registry:
            lab.return_value.endpoint = "http://127.0.0.1:4566"
            kube.main()
            registry.assert_called_once_with(lab.return_value)
            constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
