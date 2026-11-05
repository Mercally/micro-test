"""Regression for Docker nodes disappearing between inventory and NodePort use."""
import json
import unittest
from unittest.mock import Mock, patch

from kube import DOCKER, current_node_containers, nodeport_target

CLUSTER = "micro-test-local-50dacdf7"
NETWORK = "k3d-" + CLUSTER


def result(stdout="", stderr="", code=0):
    return Mock(stdout=stdout, stderr=stderr, returncode=code)


def container(cid="new-id", role="agent", address="172.19.0.6"):
    return {"Id": cid, "Name": "/worker", "State": {"Running": True},
            "Config": {"Hostname": "worker", "Labels": {"k3d.cluster": CLUSTER, "k3d.role": role}},
            "NetworkSettings": {"Networks": {NETWORK: {"IPAddress": address}}}}


class DiscoveryTests(unittest.TestCase):
    def test_disappeared_id_triggers_fresh_inventory(self):
        replies = [result(), result("old-id"), result(stderr="error: no such object: old-id", code=1),
                   result(), result("new-id"), result(json.dumps([container()]))]
        with patch("kube.run", side_effect=replies) as run:
            self.assertEqual(current_node_containers(CLUSTER)[0]["Id"], "new-id")
        inspected = [c.args[0][-1] for c in run.call_args_list if "inspect" in c.args[0]]
        self.assertEqual(inspected, ["old-id", "new-id"])
        for call in run.call_args_list:
            if "ps" in call.args[0]:
                self.assertTrue(any(x in call.args[0] for x in ("label=k3d.role=agent", "label=k3d.role=server")))

    def test_daemon_error_is_not_retried_or_hidden(self):
        with patch("kube.run", side_effect=[result(), result("id"), result(stderr="permission denied", code=1)]) as run:
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                current_node_containers(CLUSTER)
        self.assertEqual(run.call_count, 3)

    def test_churn_is_bounded_and_cannot_pass_empty(self):
        replies = [result(), result("id"), result(stderr="No such container: id", code=1)] * 3
        with patch("kube.run", side_effect=replies) as run:
            with self.assertRaisesRegex(RuntimeError, "three discovery attempts"):
                current_node_containers(CLUSTER)
        self.assertEqual(run.call_count, 9)

    def test_helper_or_foreign_container_rejected(self):
        for value in (container(role="loadbalancer"), container(role="tools")):
            with patch("kube.run", side_effect=[result(), result("id"), result(json.dumps([value]))]):
                with self.assertRaisesRegex(ValueError, "identity changed"):
                    current_node_containers(CLUSTER)

    def test_target_uses_current_ready_nodes_and_new_container(self):
        kube = Mock(cluster=CLUSTER)
        kube.call.return_value = result(json.dumps({"items": [{"metadata": {"name": "worker"},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]}}]}))
        with patch("kube.current_node_containers", return_value=[container()]) as discover:
            target = nodeport_target(kube, NETWORK)
        discover.assert_called_once_with(CLUSTER)
        self.assertEqual(target["containerID"], "new-id")
        self.assertEqual(target["ip"], "172.19.0.6")

    def test_missing_ready_mapping_and_external_ip_fail(self):
        kube = Mock(cluster=CLUSTER)
        for ready, value in ((False, container()), (True, container(address="8.8.8.8")),
                             (True, container(address=""))):
            kube.call.return_value = result(json.dumps({"items": [{"metadata": {"name": "worker"},
                "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}]}))
            with self.subTest(ready=ready, value=value), patch("kube.current_node_containers", return_value=[value]):
                with self.assertRaises(ValueError):
                    nodeport_target(kube, NETWORK)


if __name__ == "__main__":
    unittest.main()
