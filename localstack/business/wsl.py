#!/usr/bin/env python3
"""Verify owned WSL Docker/k3d cluster bindings for the canonical launcher."""
import json
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "scripts")]
from business import PREFIX


def select_cluster(clusters, network):
    """Select by observed lab network; verify Docker/EC2 binding separately."""
    candidates = [c for c in clusters if c.get("name", "").startswith(PREFIX)
                  and c.get("network", {}).get("name") == network]
    if len(candidates) != 1:
        observed = [{"name": c.get("name"), "network": c.get("network", {}).get("name")}
                    for c in clusters]
        raise ValueError("Expected one lab k3d cluster on selected network; observed: " + json.dumps(observed))
    return candidates[0]["name"]


def runtime_binding(lab, eks_name, cluster, network, containers):
    """Bind k3d agents to owned EKS instances, and API port to its Docker LB."""
    name = cluster["name"]
    owned = {c["Name"].lstrip("/"): c for c in containers
             if c.get("Config", {}).get("Labels", {}).get("k3d.cluster") == name}
    agents, ports = {}, []
    for node in cluster.get("nodes", []):
        container = owned.get(node["name"])
        if not container or network not in container["NetworkSettings"]["Networks"]:
            raise ValueError("k3d node is not on the selected Docker network: " + node["name"])
        if container["Config"]["Labels"].get("k3d.role") != node["role"]:
            raise ValueError("k3d/Docker role mismatch: " + node["name"])
        if node["role"] == "loadbalancer":
            ports.extend(int(p["HostPort"]) for p in container["NetworkSettings"]["Ports"].get("6443/tcp", []) or []
                         if p["HostIp"] in ("0.0.0.0", "127.0.0.1"))
        if node["role"] == "agent":
            # LocalStack may append flags from preceding groups. Last flag wins.
            provider_flags = [arg.split("provider-id=", 1)[1] for arg in node.get("Cmd", []) or []
                              if arg.startswith("--kubelet-arg=provider-id=")]
            if not provider_flags or not re.fullmatch(r"aws://[^/]+/i-[0-9a-f]+", provider_flags[-1]):
                raise ValueError("Cannot correlate k3d worker with EKS EC2 instance: " + node["name"])
            agents[node["name"]] = provider_flags[-1].rsplit("/", 1)[1]
    if len(set(ports)) != 1 or not agents or len(set(agents.values())) != len(agents):
        raise ValueError("Expected unique published API port and distinct EKS worker instance IDs")
    reservations = lab.client("ec2").describe_instances(InstanceIds=sorted(agents.values()))["Reservations"]
    instances = {i["InstanceId"]: i for r in reservations for i in r["Instances"]}
    if set(instances) != set(agents.values()):
        raise ValueError("EKS worker instances missing from LocalStack EC2")
    for instance in instances.values():
        tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
        if (tags.get("eks:cluster-name") != eks_name
                or tags.get("kubernetes.io/cluster/" + eks_name) != "owned"):
            raise ValueError("Docker worker belongs to another EKS cluster: " + instance["InstanceId"])
    return {"apiPort": ports[0], "workerInstanceIDs": agents, "ownedEKSInstancesMatched": True}


def required_tools(inspect_only):
    names = ("docker", "k3d", "kubectl") + (() if inspect_only else ("istioctl",))
    missing = [name for name in names if not shutil.which(name)]
    if missing:
        raise ValueError("Missing WSL executables: " + ", ".join(missing)
                         + ". See localstack/README.md. --inspect does not require istioctl.")
