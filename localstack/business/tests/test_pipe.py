"""Validate Pipe lifecycle calls against the actual botocore service model."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

import boto3
from botocore.stub import Stubber

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import business

ROLE = "arn:aws:iam::000000000000:role/micro-test-local-pipe-role"
ARN = "arn:aws:pipes:us-east-1:000000000000:pipe/" + business.PIPE
OUTPUTS = {"PipeRoleArn": ROLE, "QueueArn": "arn:aws:sqs:us-east-1:000000000000:micro-test-local-purchases",
           "StateMachineArn": "arn:aws:states:us-east-1:000000000000:stateMachine:micro-test-local-purchases"}


class PipeTests(unittest.TestCase):
    def test_required_role_in_both_lifecycle_updates(self):
        client = boto3.client("pipes", region_name="us-east-1", endpoint_url="http://127.0.0.1:4566",
                              aws_access_key_id="test", aws_secret_access_key="test")
        lab = Mock()
        lab.outputs.return_value = OUTPUTS
        lab.client.return_value = client
        current = {"Arn": ARN, "Name": business.PIPE, "Source": OUTPUTS["QueueArn"],
                   "Target": OUTPUTS["StateMachineArn"], "RoleArn": ROLE, "CurrentState": "CREATING"}
        with Stubber(client) as stub:
            for desired in ("STOPPED", "RUNNING"):
                stub.add_response("describe_pipe", current, {"Name": business.PIPE})
                stub.add_response("list_tags_for_resource", {"tags": {"Lab": "micro-test-local"}}, {"resourceArn": ARN})
                stub.add_response("update_pipe", {"Name": business.PIPE},
                                  {"Name": business.PIPE, "RoleArn": ROLE, "DesiredState": desired})
                stub.add_response("describe_pipe", current | {"CurrentState": desired}, {"Name": business.PIPE})
                self.assertEqual(business.pipe(lab, desired)["CurrentState"], desired)
            stub.assert_no_pending_responses()

    def test_foreign_role_prevents_update(self):
        lab = Mock()
        lab.outputs.return_value = OUTPUTS
        lab.client.return_value.describe_pipe.return_value = {
            "Source": OUTPUTS["QueueArn"], "Target": OUTPUTS["StateMachineArn"], "RoleArn": ROLE + "-foreign"}
        with self.assertRaisesRegex(ValueError, "Unowned or modified"):
            business.pipe(lab, "RUNNING")
        lab.client.return_value.update_pipe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
