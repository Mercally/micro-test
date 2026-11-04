import base64
import hashlib
import hmac
import json
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda"))
from authorizer import verify
from transport import normalize, InvalidEvent
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

    def test_normalizes_actual_sqs_batch_without_trusting_extra_fields(self):
        sale = str(uuid.uuid4())
        event = {"type": "PurchaseAccepted", "version": 1, "saleId": sale, "correlationId": sale,
                 "eventId": str(uuid.uuid4()), "generation": 1, "owner": "attacker"}
        value = normalize([{"eventSource": "aws:sqs", "body": json.dumps(event)}], "execution-1")
        self.assertEqual(value["owner"], "execution-1")
        self.assertEqual(value["saleId"], sale)
        for body in (event | {"version": 2}, event | {"version": True}, event | {"saleId": "invalid"}, [], None):
            with self.assertRaises(InvalidEvent): normalize([{"eventSource": "aws:sqs", "body": json.dumps(body)}], "owner")
        for records in ([], [{}, {}], [None], [{"eventSource": "aws:sqs", "body": "invalid JSON"}]):
            with self.assertRaises(InvalidEvent): normalize(records, "owner")

    def test_runtime_cannot_target_aws_or_old_host(self):
        for value in ("https://sqs.us-east-1.amazonaws.com", "http://100.95.224.37:4566", "http://169.254.169.254:4566", "http://user@127.0.0.1:4566", "http://127.0.0.1:4566/evil"):
            with self.assertRaises(ValueError): local_url(value, 4566)
        self.assertEqual(local_url("http://172.18.0.2:4566", 4566), "http://172.18.0.2:4566")


if __name__ == "__main__": unittest.main()
