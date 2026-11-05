"""Regression: LocalStack-generated k3d name and read-only WSL inspection."""
import base64
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wsl
from kube import Kube, verify_cluster_config

NAME = "micro-test-local-50dacdf7"
NETWORK = "k3d-" + NAME
CA = base64.b64encode(b"lab-ca-fixture").decode()


def config():
    return {"clusters": [{"cluster": {"server": "https://0.0.0.0:43517", "certificate-authority-data": CA}}],
            "users": [{"user": {"client-certificate-data": "fixture-cert", "client-key-data": "fixture-key"}}]}


def runtime():
    nodes = [{"name": "k3d-" + NAME + "-serverlb", "role": "loadbalancer"}]
    instances = []
    # Public instance identifiers from the user's observed local cluster.
    ids = ["i-ff10bf3fdd4c972a8", "i-bf062470f87f606d8", "i-faec35d8d5f9c9493"]
    for number, instance in enumerate(ids):
        nodes.append({"name": "k3d-" + NAME + "-agent-" + str(number), "role": "agent",
                      "Cmd": ["agent"] + ["--kubelet-arg=provider-id=aws://us-east-1a/" + i for i in ids[:number+1]]})
        instances.append({"InstanceId": instance, "Tags": [
            {"Key": "eks:cluster-name", "Value": "micro-test-local-eks"},
            {"Key": "kubernetes.io/cluster/micro-test-local-eks", "Value": "owned"}]})
    cluster = {"name": NAME, "network": {"name": NETWORK}, "nodes": nodes}
    containers = [{"Name": "/" + n["name"], "Config": {"Image": "rancher/k3s:fixture", "Labels": {"k3d.cluster": NAME, "k3d.role": n["role"]}},
                   "NetworkSettings": {"Networks": {NETWORK: {}}, "Ports": {"6443/tcp": [
                       {"HostIp": "0.0.0.0", "HostPort": "43517"}]} if n["role"] == "loadbalancer" else {}}}
                  for n in nodes]
    lab = Mock()
    lab.client.return_value.describe_instances.return_value = {"Reservations": [{"Instances": instances}]}
    return lab, cluster, containers


class WslDiscoveryTests(unittest.TestCase):
    def test_generated_name_selected_from_observed_network(self):
        clusters = [{"name": "micro-test-local-other", "network": {"name": "elsewhere"}},
                    {"name": NAME, "network": {"name": NETWORK}, "env": ["SECRET=must-not-print"]}]
        self.assertEqual(wsl.select_cluster(clusters, NETWORK), NAME)

    def test_ambiguous_or_foreign_cluster_rejected_without_leaking_env(self):
        for clusters in ([{"name": NAME, "network": {"name": NETWORK}, "env": ["SECRET=hidden"]}] * 2,
                         [{"name": "foreign", "network": {"name": NETWORK}}], []):
            with self.subTest(clusters=clusters), self.assertRaises(ValueError) as error:
                wsl.select_cluster(clusters, NETWORK)
            self.assertNotIn("hidden", str(error.exception))

    def test_internal_ca_preserved_with_observed_network_and_port(self):
        value = config()
        verify_cluster_config(NAME, value, NETWORK, 43517)
        self.assertEqual(value["clusters"][0]["cluster"]["server"], "https://127.0.0.1:43517")
        self.assertEqual(value["clusters"][0]["cluster"]["certificate-authority-data"], CA)
        for network, port in ((None, None), ("foreign-network", 43517), (NETWORK, 4511)):
            with self.subTest(network=network, port=port), self.assertRaises(ValueError):
                verify_cluster_config(NAME, config(), network, port)

    def test_other_lab_cluster_and_unsafe_kubeconfig_rejected(self):
        bad = []
        value = config(); value["users"][0]["user"]["exec"] = {"command": "aws"}; bad.append(value)
        value = config(); value["clusters"][0]["cluster"]["server"] = "https://eks.amazonaws.com:443"; bad.append(value)
        value = config(); value["clusters"][0]["cluster"]["insecure-skip-tls-verify"] = True; bad.append(value)
        value = config(); value["clusters"][0]["cluster"]["certificate-authority-data"] = ""; bad.append(value)
        value = config(); value["clusters"][0]["cluster"]["proxy-url"] = "http://external:8080"; bad.append(value)
        value = config(); value["clusters"].append(copy.deepcopy(value["clusters"][0])); bad.append(value)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                verify_cluster_config(NAME, value, NETWORK, 43517)
        with self.assertRaises(ValueError):
            verify_cluster_config("micro-test-local-capd", config(), NETWORK, 43517)

    def test_runtime_uses_last_provider_id_and_owned_ec2_tags(self):
        lab, cluster, containers = runtime()
        result = wsl.runtime_binding(lab, "micro-test-local-eks", cluster, NETWORK, containers)
        self.assertEqual(result["apiPort"], 43517)
        self.assertEqual(len(set(result["workerInstanceIDs"].values())), 3)
        lab.client.return_value.describe_instances.assert_called_once_with(
            InstanceIds=sorted(result["workerInstanceIDs"].values()))

    def test_runtime_rejects_foreign_eks_instances_and_missing_workers(self):
        for mutation in ("foreign", "missing", "duplicate"):
            lab, cluster, containers = runtime()
            if mutation == "foreign":
                lab.client.return_value.describe_instances.return_value["Reservations"][0]["Instances"][0]["Tags"] = []
            elif mutation == "missing":
                lab.client.return_value.describe_instances.return_value["Reservations"][0]["Instances"].pop()
            else:
                cluster["nodes"][-1]["Cmd"] = cluster["nodes"][-2]["Cmd"]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                wsl.runtime_binding(lab, "micro-test-local-eks", cluster, NETWORK, containers)

    def test_runtime_rejects_wrong_docker_network_and_unpublished_api(self):
        for mutation in ("network", "port"):
            lab, cluster, containers = runtime()
            containers[0]["NetworkSettings"]["Networks" if mutation == "network" else "Ports"] = {}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                wsl.runtime_binding(lab, "micro-test-local-eks", cluster, NETWORK, containers)

    def test_missing_tools_reported_together_and_inspect_does_not_require_istio(self):
        with patch.object(wsl.shutil, "which", side_effect=lambda name: None if name in ("k3d", "istioctl") else "/bin/" + name):
            with self.assertRaisesRegex(ValueError, "k3d, istioctl"):
                wsl.required_tools(False)
        with patch.object(wsl.shutil, "which", side_effect=lambda name: None if name == "istioctl" else "/bin/" + name):
            wsl.required_tools(True)

    def test_node_inspection_reads_only_and_reports_actual_taints(self):
        kube = Kube.__new__(Kube)
        node = {"metadata": {"name": "worker", "labels": {"lab-role": "terceros", "lab": "micro-test-local"}},
                "spec": {"taints": []}, "status": {"conditions": [{"type": "Ready", "status": "True"}],
                                                   "nodeInfo": {"kubeletVersion": "v1.34.1+k3s1"}}}
        kube.call = Mock(side_effect=[Mock(stdout=json.dumps({"items": [node]})),
                                     Mock(stdout=json.dumps({"serverVersion": {"gitVersion": "v1.34.1+k3s1"}}))])
        result = kube.inspect()
        self.assertTrue(result["nodes"][0]["ready"])
        self.assertEqual(result["nodes"][0]["taints"], [])
        self.assertEqual([c.args[0] for c in kube.call.call_args_list], ["get", "version"])
        for call in kube.call.call_args_list:
            self.assertIn("--request-timeout=30s", call.args)



if __name__ == "__main__":
    unittest.main()
