"""Offline regression checks for endpoint isolation and dependency/fixture contracts."""
import importlib.util
import json
import os
import unittest
from unittest.mock import patch, Mock
from common import ROOT, Lab, endpoint_url, NoRedirect
from lab import FILES, template, cleanup


class SafetyTests(unittest.TestCase):
    def test_rejects_real_aws_and_endpoint_ambiguities(self):
        for value in ("https://s3.amazonaws.com", "http://127.0.0.1:4566@aws.amazon.com",
                      "http://100.95.224.38:4566", "http://100.100.154.69:4566", "http://127.0.0.1:443",
                      "http://127.0.0.1:4566/foo", "http://127.0.0.1:4566?host=aws",
                      "http://localhost.evil.test:4566", "http://localhost:4566#fragment"):
            with self.subTest(value=value), self.assertRaises(ValueError): endpoint_url(value)

    def test_selected_laptop_host_is_allowed_without_opening_tailnet(self):
        self.assertEqual(endpoint_url("http://100.100.154.68:4566/"), "http://100.100.154.68:4566")
        for value in ("http://100.100.154.68:4567", "http://100.100.154.68.evil.test:4566",
                      "http://100.100.154.67:4566", "http://100.64.0.1:4566"):
            with self.subTest(value=value), self.assertRaises(ValueError): endpoint_url(value)

    def test_http_redirect_rejected(self):
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://aws.amazon.com")

    def test_non_localstack_rejected_before_sdk(self):
        with patch("common.http", return_value={}), patch("boto3.Session") as session:
            with self.assertRaises(ValueError): Lab("http://127.0.0.1:4566")
            session.assert_not_called()

    def test_ambient_aws_credentials_removed_and_gateway_pinned(self):
        health={"services":{},"version":"test"}
        info={"edition":"pro","version":"test"}
        with patch.dict(os.environ,{"AWS_PROFILE":"production","AWS_SESSION_TOKEN":"real-session",
                                   "AWS_ROLE_ARN":"arn:aws:iam::123:role/prod"}), \
                patch("common.http",side_effect=[health,info]), patch("boto3.Session") as session:
            session.return_value.client.return_value.get_caller_identity.return_value={"Account":"000000000000"}
            lab=Lab("http://127.0.0.1:4566")
            self.assertNotIn("AWS_PROFILE",os.environ)
            self.assertNotIn("AWS_SESSION_TOKEN",os.environ)
            self.assertNotIn("AWS_ROLE_ARN",os.environ)
            lab.client("s3")
            kwargs=session.return_value.client.call_args.kwargs
            self.assertEqual(kwargs["endpoint_url"],"http://127.0.0.1:4566")
            guard=session.return_value.client.return_value.meta.events.register.call_args.args[1]
            with self.assertRaises(ValueError):guard(Mock(url="https://s3.amazonaws.com/bucket"))
            with self.assertRaises(ValueError):lab.client("s3",("AKIAREALKEY","secret"))

    def test_default_cleanup_preserves_data_and_pipe_dependencies(self):
        lab=Mock()
        lab.stack.return_value={"StackStatus":"CREATE_COMPLETE"}
        with patch("lab.owned_pipe",return_value={"Name":"micro-test-local-diagnostic"}), \
                patch("lab.set_pipe_state") as state, patch("lab.wait_stack"), patch("builtins.print"):
            cleanup(lab,False)
        state.assert_called_once_with(lab,"STOPPED")
        deleted={c.kwargs["StackName"] for c in lab.client.return_value.delete_stack.call_args_list}
        self.assertEqual(deleted,{"micro-test-local-api","micro-test-local-micro-api"})
        lab.client.return_value.delete_pipe.assert_not_called()

    def test_rendered_fixtures_and_stopped_pipe(self):
        for path in FILES:
            self.assertNotIn("RENDER_FROM_LOCAL",json.dumps(template(path)))
        pipe=json.loads((ROOT/"fixtures/pipe.json").read_text())
        self.assertEqual(pipe["DesiredState"],"STOPPED")
        workflow=json.loads((ROOT/"fixtures/diagnostic.asl.json").read_text())
        self.assertEqual(workflow["States"][workflow["StartAt"]]["Type"],"Task")

    def test_fixture_preserves_api_and_sqs_payload(self):
        spec=importlib.util.spec_from_file_location("diagnostic",ROOT/"fixtures/diagnostic.py")
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        payload={"diagnosticId":"proof","value":73}
        expected=module.handler(payload,None)
        self.assertEqual(module.handler([{"body":json.dumps(payload)}],None),expected)
        proxy=module.handler({"httpMethod":"POST","body":json.dumps(payload)},None)
        self.assertEqual(json.loads(proxy["body"]),expected)


if __name__=="__main__":unittest.main()
