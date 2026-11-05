"""Regression: Lambda bridge transport must survive replacement Lambda containers."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lambda_network as repair
import wsl

CLUSTER = "micro-test-local-50dacdf7"
NETWORK = "k3d-" + CLUSTER


def localstack():
    return {"Config": {"Image": "localstack/localstack-pro:fixture", "Env": []},
            "NetworkSettings": {"Networks": {"bridge": {"IPAddress": "172.17.0.2"},
                                             NETWORK: {"IPAddress": "172.19.0.5"}}}}


class LambdaNetworkTests(unittest.TestCase):
    def test_actual_default_bridge_selected_instead_of_k3d(self):
        self.assertEqual(repair.lambda_network(localstack(), NETWORK), ("bridge", "172.17.0.2"))
        configured = localstack()
        configured["Config"]["Env"] = ["LOCALSTACK_LAMBDA_DOCKER_NETWORK=" + NETWORK]
        self.assertEqual(repair.lambda_network(configured, NETWORK), (NETWORK, "172.19.0.5"))

    def test_unknown_network_or_runtime_override_rejected(self):
        for env in (["LAMBDA_DOCKER_NETWORK=unrelated"], ["LAMBDA_DOCKER_FLAGS=--network other"],
                    ["ENABLE_CONFIG_UPDATES=1"], ["LAMBDA_DOCKER_NETWORK=bridge", "LOCALSTACK_LAMBDA_DOCKER_NETWORK=" + NETWORK]):
            container = localstack()
            container["Config"]["Env"] = env
            with self.subTest(env=env), self.assertRaises(ValueError):
                repair.lambda_network(container, NETWORK)

    def test_absent_or_nonlocal_address_rejected(self):
        for value in ("", "8.8.8.8", "127.0.0.1", "169.254.169.254", "::1"):
            container = localstack()
            container["NetworkSettings"]["Networks"]["bridge"]["IPAddress"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                repair.lambda_network(container, NETWORK)

    def run_prepare(self, attached=False, foreign=False, wrong_db=False):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / ".state") as temp:
            root = Path(temp)
            path = root / "runtime.json"
            original = {"endpoint": "http://127.0.0.1:4566", "databaseMode": "rds", "DatabasePort": "4510",
                        "DatabaseHost": "172.19.0.5", "RuntimeEndpoint": "http://172.19.0.5:4566",
                        "MicroBaseUrl": "http://172.19.0.7:31080"}
            path.write_text(json.dumps(original))
            lab = Mock(endpoint="http://127.0.0.1:4566")
            lab.client.return_value.describe_db_instances.return_value = {"DBInstances": [
                {"DBInstanceStatus": "available", "Endpoint": {"Port": 9999 if wrong_db else 4510}}]}
            kube = Mock(cluster=CLUSTER, report_dir=root / "report")
            node = {"State": {"Running": True}, "Config": {"Labels": {
                "k3d.cluster": "foreign" if foreign else CLUSTER, "k3d.role": "agent"}},
                "NetworkSettings": {"Networks": {NETWORK: {"IPAddress": "172.19.0.7"}}}}
            if attached:
                node["NetworkSettings"]["Networks"]["bridge"] = {"IPAddress": "172.17.0.5"}
            old_target = {"containerID": "owned-node-id", "node": "owned-worker", "ip": "172.19.0.7", "network": NETWORK}
            new_target = old_target | {"ip": "172.17.0.5", "network": "bridge"}
            def docker(cmd):
                args = cmd[len(repair.DOCKER):]
                if args == ["inspect", "localstack-id"]:
                    value = [localstack()]
                elif args == ["network", "inspect", "bridge"]:
                    value = [{"Name": "bridge", "Driver": "bridge", "Scope": "local"}]
                elif args == ["inspect", "owned-node-id"]:
                    value = [node]
                elif args == ["network", "connect", "--help"]:
                    return Mock(stdout="--gw-priority")
                elif args == ["network", "connect", "--gw-priority", "-1", "bridge", "owned-node-id"]:
                    return Mock(stdout="")
                else:
                    raise AssertionError("Unexpected Docker command: " + repr(args))
                return Mock(stdout=json.dumps(value))
            with patch.object(repair, "run", side_effect=docker) as run, \
                    patch.object(repair, "current_node_containers", return_value=[node]), \
                    patch.object(repair, "nodeport_target", side_effect=[new_target] if attached else [old_target, new_target]), \
                    patch("builtins.print"):
                if foreign or wrong_db:
                    with self.assertRaises(ValueError):
                        repair.prepare(lab, kube, "localstack-id", NETWORK, path)
                    self.assertEqual(json.loads(path.read_text()), original)
                else:
                    result = repair.prepare(lab, kube, "localstack-id", NETWORK, path)
                    self.assertEqual(result["status"], "PREPARED")
                    actual = json.loads(path.read_text())
                    self.assertEqual(actual["RuntimeEndpoint"], "http://172.17.0.2:4566")
                    self.assertEqual(actual["DatabaseHost"], "172.17.0.2")
                    self.assertEqual(actual["MicroBaseUrl"], "http://172.17.0.5:31080")
                    self.assertEqual(actual["DatabasePort"], "4510")
                    self.assertEqual(json.loads((kube.report_dir / "business-runtime-before-network.json").read_text()), original)
                mutations = [c.args[0] for c in run.call_args_list if "connect" in c.args[0] and "--help" not in c.args[0]]
                self.assertEqual(len(mutations), 0 if attached or foreign or wrong_db else 1)

    def test_repair_attaches_only_owned_node_and_preserves_database(self):
        self.run_prepare()

    def test_already_attached_node_needs_no_network_mutation(self):
        self.run_prepare(attached=True)

    def test_foreign_node_and_changed_database_refuse_before_mutation(self):
        self.run_prepare(foreign=True)
        self.run_prepare(wrong_db=True)

    def test_repair_mode_skips_bootstrap_and_never_starts_triggers(self):
        for failure in (None, RuntimeError("Lambda health failed")):
            lab = Mock()
            lab.stack.side_effect = [None, {"StackStatus": "CREATE_COMPLETE"}]
            lab.outputs.return_value = {"ClusterName": "micro-test-local-eks"}
            lab.client.return_value.describe_cluster.return_value = {"cluster": {"version": "1.34"}}
            lab.client.return_value.describe_rule.return_value = {"State": "DISABLED"}
            lab.health = {}
            container = localstack() | {"Id": "local", "Name": "/localstack-aws"}
            container["NetworkSettings"]["Ports"] = {"4566/tcp": [{"HostPort": "4566"}]}
            cluster = {"name": CLUSTER, "network": {"name": NETWORK}}
            responses = [Mock(stdout="local"), Mock(stdout=json.dumps([container])), Mock(stdout=json.dumps([cluster]))]
            with self.subTest(failure=failure), \
                    patch.object(sys, "argv", ["wsl.py", "--repair-lambda-network", "--network", NETWORK]), \
                    patch.object(sys, "platform", "linux"), patch.object(Path, "read_text", return_value="microsoft-WSL2"), \
                    patch.object(wsl, "required_tools"), patch.object(wsl, "Lab", return_value=lab), \
                    patch.object(wsl, "run", side_effect=responses), patch.object(wsl, "Kube") as constructor, \
                    patch.object(wsl, "runtime_binding", return_value={"apiPort": 43517}), \
                    patch.object(wsl, "bootstrap") as bootstrap, patch.object(wsl, "registry") as registry, \
                    patch.object(wsl, "prepare_lambda_network") as prepare, \
                    patch.object(wsl, "deploy", side_effect=failure) as deploy, \
                    patch.object(wsl, "pipe", return_value={"CurrentState": "STOPPED"}) as pipe, \
                    patch.object(wsl, "publisher_schedule", return_value={"State": "DISABLED"}) as schedule, \
                    patch.object(wsl, "save") as save, patch("builtins.print"):
                constructor.return_value.report_dir = Path("/tmp/not-written")
                if failure:
                    with self.assertRaisesRegex(RuntimeError, "health failed"):
                        wsl.main()
                    save.assert_not_called()
                else:
                    wsl.main()
                    self.assertEqual(save.call_args.args[1]["purchaseFlow"], "PENDING")
                bootstrap.assert_not_called()
                registry.assert_not_called()
                prepare.assert_called_once()
                self.assertTrue(deploy.call_args.args[1].update)
                pipe.assert_called_once_with(lab)
                schedule.assert_called_once_with(lab)
                lab.client.return_value.enable_rule.assert_not_called()


if __name__ == "__main__":
    unittest.main()
