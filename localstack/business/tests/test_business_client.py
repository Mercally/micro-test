"""Client-side regressions observed against the laptop runtime."""
import base64
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import business


class BusinessClientTests(unittest.TestCase):
    def scheduler_fixture(self):
        lab = Mock()
        cf, events, scheduler = Mock(), Mock(), Mock()
        lab.client.side_effect = {"cloudformation": cf, "events": events, "scheduler": scheduler}.__getitem__
        lab.stack.return_value = {"StackStatus": "CREATE_COMPLETE"}
        lab.outputs.return_value = {"RoleArn": "local-scheduler-role"}
        cf.describe_stack_resource.return_value = {"StackResourceDetail": {
            "PhysicalResourceId": "micro-test-local-publisher", "ResourceType": "AWS::Events::Rule"}}
        cf.get_template.return_value = {"TemplateBody": business.template("publisher-scheduler")}
        events.describe_rule.return_value = {"ScheduleExpression": "rate(1 minute)"}
        target = {"Id": "publisher", "Arn": "arn:aws:lambda:us-east-1:000000000000:function:micro-test-local-publisher",
                  "Input": '{"limit":20}'}
        events.list_targets_by_rule.return_value = {"Targets": [target]}
        events.remove_targets.return_value = {"FailedEntryCount": 0}
        config = {"Name": "micro-test-local-publisher", "GroupName": "default", "State": "ENABLED",
                  "Arn": "local-schedule-arn", "ScheduleExpression": "rate(1 minute)",
                  "ScheduleExpressionTimezone": "UTC", "FlexibleTimeWindow": {"Mode": "OFF"},
                  "Description": "preserve this", "Target": {k: v for k, v in target.items() if k != "Id"}}
        config["Target"].update(RoleArn="local-scheduler-role", RetryPolicy={"MaximumRetryAttempts": 0})
        return lab, events, scheduler, config

    def test_pause_detaches_broken_rule_and_preserves_scheduler_options(self):
        lab, events, scheduler, config = self.scheduler_fixture()
        scheduler.get_schedule.side_effect = [config, config | {"State": "DISABLED"}]
        self.assertEqual(business.publisher_schedule(lab, False)["State"], "DISABLED")
        events.remove_targets.assert_called_once_with(Rule="micro-test-local-publisher", Ids=["publisher"])
        kwargs = scheduler.update_schedule.call_args.kwargs
        self.assertEqual(kwargs["State"], "DISABLED")
        self.assertEqual(kwargs["Target"], config["Target"])
        self.assertEqual(kwargs["Description"], "preserve this")
        self.assertEqual(kwargs["FlexibleTimeWindow"], {"Mode": "OFF"})

    def test_foreign_legacy_target_prevents_mutation(self):
        lab, events, scheduler, _ = self.scheduler_fixture()
        events.list_targets_by_rule.return_value = {"Targets": [{"Id": "foreign"}]}
        with self.assertRaisesRegex(ValueError, "preserve user changes"):
            business.publisher_schedule(lab, True)
        events.remove_targets.assert_not_called()
        events.disable_rule.assert_not_called()
        scheduler.update_schedule.assert_not_called()

    def test_schedule_status_is_read_only(self):
        lab, events, scheduler, config = self.scheduler_fixture()
        scheduler.get_schedule.return_value = config
        self.assertEqual(business.publisher_schedule(lab)["State"], "ENABLED")
        events.remove_targets.assert_not_called()
        scheduler.update_schedule.assert_not_called()

    def test_token_uses_emulator_clock_even_if_client_clock_is_ahead(self):
        lab = Mock()
        lab.outputs.return_value = {"AuthSecretArn": "local-secret"}
        lab.client.return_value.get_secret_value.return_value = {
            "SecretString": '{"key":"unit-test-local-key"}',
            "ResponseMetadata": {"HTTPHeaders": {"date": "Thu, 05 Nov 2026 01:00:00 GMT"}}}
        with patch.object(business.time, "time", return_value=9999999999):
            for kind in ("valid", "invalid", "expired"):
                encoded = business.token(lab, kind).split(".")[1]
                claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
                now = 1793840400
                self.assertEqual(claims["iat"], now - 3600 if kind == "expired" else now)
                self.assertEqual(claims["exp"], now - 1 if kind == "expired" else now + 3600)


if __name__ == "__main__":
    unittest.main()
