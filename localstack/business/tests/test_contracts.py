import base64
import hashlib
import hmac
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda"))
from authorizer import verify
from runtime import local_url


def jwt(key="local-test-key", **claims):
    def encode(value): return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    body = {"iss": "micro-test-local", "aud": "micro-test-local-purchases", "sub": "tester", "iat": 100, "exp": 200} | claims
    message = encode({"alg": "HS256", "typ": "JWT"}) + "." + encode(body)
    return message + "." + base64.urlsafe_b64encode(hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()).decode().rstrip("=")


class ContractsTest(unittest.TestCase):
    def test_auth_signature_expiry_and_claims(self):
        self.assertEqual(verify(jwt(), "local-test-key", now=150)["sub"], "tester")
        for token in (jwt("wrong"), jwt(exp=150), jwt(aud="other"), jwt(iss="other"), jwt(iat=151), jwt(exp=True), jwt(exp=4000), "", "a.b.c"):
            with self.subTest(token=token[:8]), self.assertRaises(ValueError):
                verify(token, "local-test-key", now=150)

    def test_runtime_cannot_target_aws_or_old_host(self):
        for value in ("https://sqs.us-east-1.amazonaws.com", "http://100.95.224.37:4566", "http://169.254.169.254:4566", "http://user@127.0.0.1:4566", "http://127.0.0.1:4566/evil"):
            with self.assertRaises(ValueError): local_url(value, 4566)
        self.assertEqual(local_url("http://172.18.0.2:4566", 4566), "http://172.18.0.2:4566")


if __name__ == "__main__": unittest.main()
