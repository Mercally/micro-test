"""Lease + admission contract of the Lambda consumer embedded in aws/cloudformation/04-messaging.yaml.

Run: python3 -m unittest aws/tests/test_consumer.py   (no AWS, no boto3 needed)
"""
import json
import os
import re
import sys
import textwrap
import types
import unittest

TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "cloudformation", "04-messaging.yaml")
SM = "arn:aws:states:us-east-1:111:stateMachine:micro-test-purchase"


def load_consumer(fake_client):
    source = open(TEMPLATE).read()
    block = re.search(r"ZipFile: \|\n((?: {10}.*\n|\n)+)", source).group(1)
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda name: fake_client
    sys.modules["boto3"] = boto3
    os.environ.update(STATE_MACHINE_ARN=SM, MAX_RECEIVE_COUNT="3", RELEASE_DELAY_SECONDS="30", POLL_SECONDS="0")
    module = types.ModuleType("consumer")
    exec(textwrap.dedent(block), module.__dict__)
    return module


class AlreadyExists(Exception):
    pass


class FakeSfn:
    def __init__(self, fail=None, statuses=("RUNNING", "SUCCEEDED")):
        self.started, self.described, self.fail = [], [], fail
        self.statuses = list(statuses)
        self.exceptions = types.SimpleNamespace(ExecutionAlreadyExists=AlreadyExists)

    def start_execution(self, **kwargs):
        self.started.append(kwargs)
        if self.fail:
            raise self.fail
        return {"executionArn": "arn:exec:" + kwargs["name"]}

    def describe_execution(self, executionArn):
        self.described.append(executionArn)
        return {"status": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]}


class Context:
    def __init__(self, remaining_ms=300_000, step_ms=0):
        self.remaining, self.step = remaining_ms, step_ms

    def get_remaining_time_in_millis(self):
        self.remaining -= self.step
        return self.remaining


def record(message_id="m-1", count="1"):
    return {"messageId": message_id, "receiptHandle": "rh-" + count,
            "attributes": {"ApproximateReceiveCount": count},
            "body": json.dumps({"saleId": "s", "eventId": "e", "correlationId": "c", "type": "x"})}


class ConsumerTest(unittest.TestCase):
    def test_starts_named_execution_waits_for_it_and_never_lets_esm_delete(self):
        sfn = FakeSfn(statuses=("RUNNING", "RUNNING", "SUCCEEDED"))
        result = load_consumer(sfn).handler({"Records": [record(count="2")]}, Context())
        self.assertEqual(result, {"batchItemFailures": [{"itemIdentifier": "m-1"}]})
        call = sfn.started[0]
        self.assertEqual(call["name"], "m-1-2")
        payload = json.loads(call["input"])
        self.assertEqual(payload["receiptHandle"], "rh-2")
        self.assertEqual((payload["receiveCount"], payload["maxReceiveCount"], payload["releaseDelaySeconds"]), (2, 3, 30))
        self.assertEqual(payload["body"], {"saleId": "s", "eventId": "e", "correlationId": "c"})
        self.assertEqual(sfn.described, ["arn:exec:m-1-2"] * 3, "busy until the execution leaves RUNNING")

    def test_lambda_retry_of_same_delivery_waits_on_existing_execution(self):
        sfn = FakeSfn(fail=AlreadyExists(), statuses=("FAILED",))
        result = load_consumer(sfn).handler({"Records": [record()]}, Context())
        self.assertEqual(sfn.described, [SM.replace(":stateMachine:", ":execution:") + ":m-1-1"])
        self.assertEqual(result["batchItemFailures"], [{"itemIdentifier": "m-1"}])

    def test_stops_waiting_before_own_timeout(self):
        sfn = FakeSfn(statuses=("RUNNING",))
        load_consumer(sfn).handler({"Records": [record()]}, Context(remaining_ms=20_000, step_ms=4_000))
        self.assertLess(len(sfn.described), 5)

    def test_start_failure_and_invalid_body_keep_message_in_flight(self):
        sfn = FakeSfn(fail=RuntimeError("throttled"))
        self.assertEqual(load_consumer(sfn).handler({"Records": [record()]}, Context())["batchItemFailures"],
                         [{"itemIdentifier": "m-1"}])
        self.assertEqual(sfn.described, [])
        sfn = FakeSfn()
        bad = record()
        bad["body"] = "not json"
        result = load_consumer(sfn).handler({"Records": [bad]}, Context())
        self.assertEqual((sfn.started, result["batchItemFailures"]), ([], [{"itemIdentifier": "m-1"}]))


if __name__ == "__main__":
    unittest.main()
