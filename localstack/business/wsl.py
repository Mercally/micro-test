#!/usr/bin/env python3
"""Discover the existing laptop Docker runtime and complete its purchase deployment."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "scripts")]
from business import Lab, PREFIX, deploy, endpoint, invoke, pipe, publisher_schedule, report_dir, save
from kube import Kube, DOCKER, bootstrap, registry, run
from lambda_network import prepare as prepare_lambda_network


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
                         + ". See localstack/business/RUNBOOK.md. --inspect does not require istioctl.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-endpoint", type=endpoint, default="http://100.100.154.68:4566")
    parser.add_argument("--database", choices=["rds", "postgres"], default="rds")
    parser.add_argument("--network")
    parser.add_argument("--update", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect", action="store_true", help="Inspect actual Kubernetes nodes without deploying or requiring Istio")
    mode.add_argument("--repair-lambda-network", action="store_true", help="Repair existing runtime endpoints without rebuilding micro or restarting LocalStack; leave triggers stopped")
    args = parser.parse_args()
    if sys.platform != "linux" or "microsoft" not in Path("/proc/sys/kernel/osrelease").read_text().lower():
        raise ValueError("Run inside laptop WSL2, with its Docker Desktop integration")
    required_tools(args.inspect or args.repair_lambda_network)
    os.environ.pop("DOCKER_CONTEXT", None)
    os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    lab = Lab("http://127.0.0.1:4566")
    if lab.stack(PREFIX + "micro-api"):
        raise ValueError("Legacy micro-api exposes internal routes without auth; retire that optional API before enabling purchases")
    ids = run(DOCKER + ["ps", "-q"]).stdout.split()
    if not ids: raise ValueError("No running Docker containers")
    containers = json.loads(run(DOCKER + ["inspect"] + ids).stdout)
    candidates = [c for c in containers if c["Config"]["Image"].startswith("localstack/localstack")
                  and any(p.get("HostPort") == "4566" for p in c["NetworkSettings"]["Ports"].get("4566/tcp") or [])]
    if len(candidates) != 1: raise ValueError("Expected exactly one LocalStack container publishing laptop port 4566")
    container = candidates[0]
    # Never print or persist full Docker env: it can contain a LocalStack license token.
    selected_env = {k: v for item in container["Config"].get("Env", []) for k, _, v in [item.partition("=")]
                    if k in {"PERSISTENCE", "ENFORCE_IAM", "LAMBDA_DOCKER_NETWORK"}}
    networks = list(container["NetworkSettings"]["Networks"])
    network = args.network or ("micro-test-lab" if "micro-test-lab" in networks else networks[0] if len(networks) == 1 else None)
    if network not in networks: raise ValueError("Select one observed LocalStack network with --network: " + ", ".join(networks))
    clusters = json.loads(run(["k3d", "cluster", "list", "-o", "json"]).stdout)
    cluster = select_cluster(clusters, network)
    eks_name = lab.outputs("eks")["ClusterName"]
    eks = lab.client("eks").describe_cluster(name=eks_name)["cluster"]
    observed = next(c for c in clusters if c["name"] == cluster)
    binding = runtime_binding(lab, eks_name, observed, network, containers)
    print(json.dumps({"localstackContainer": container["Name"].lstrip("/"), "cluster": cluster, "network": network,
                      "configuration": selected_env, "persistence": lab.health.get("features")}, indent=2), flush=True)
    if selected_env.get("PERSISTENCE") != "1": print("Persistence PENDIENTE. No emulator restart will be attempted.", flush=True)
    kube = Kube(cluster, expected_network=network, expected_api_port=binding["apiPort"])
    if args.inspect:
        result = {"status": "INSPECTED", "note": "Read-only inventory; not runtime acceptance",
                  "eks": {"name": eks_name, "apiVersion": eks["version"], "k3dCluster": cluster,
                          "runtimeBinding": binding,
                          "tls": "Verified with internal k3d CA; EKS gateway certificate is separate"},
                  "tools": {name: shutil.which(name) for name in ("docker", "k3d", "kubectl", "istioctl")},
                  "kubernetes": kube.inspect()}
        path = kube.report_dir / "wsl-inspect.json"
        save(path, result)
        print(json.dumps(result, indent=2), flush=True)
        print("Report: " + str(path), flush=True)
        return
    runtime = HERE.parent / ".state/business-runtime.json"
    if args.repair_lambda_network:
        if not lab.stack(PREFIX + "business"):
            raise ValueError("Network repair requires the existing business stack")
        if (pipe(lab)["CurrentState"] != "STOPPED"
                or publisher_schedule(lab)["State"] != "DISABLED"):
            raise ValueError("Stop business triggers using business.py stop before network repair")
    else:
        bootstrap(lab, kube, argparse.Namespace(localstack_container=container["Id"], docker_network=network,
                                              database=args.database, presign_endpoint=args.client_endpoint))
        registry(lab, kube.report_dir)
    prepare_lambda_network(lab, kube, container["Id"], network, runtime)
    deploy(lab, argparse.Namespace(runtime=str(runtime), update=args.update or args.repair_lambda_network))
    if args.repair_lambda_network:
        save(kube.report_dir / "lambda-network-health.json", {
            "status": "PASS", "scope": "Publisher reaches database/outbox; transport reaches micro health after deployment",
            "purchaseFlow": "PENDING", "triggers": "STOPPED/DISABLED"})
        print("Runtime health PASS. Triggers remain stopped. Run business.py start, then http/test.", flush=True)
        print("Reports: " + str(kube.report_dir), flush=True)
        return
    invoke(lab, "publisher", {"health": True})
    invoke(lab, "transport", {"operation": "health"})
    pipe(lab, "RUNNING")
    publisher_schedule(lab, True)
    print("Purchase flow started. Generate purchase.http from the client using business.py http.", flush=True)


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        path = report_dir() / "wsl.json"
        save(path, {"status": "FAIL", "endpoint": "http://127.0.0.1:4566", "error": str(exc)})
        print("Report: " + str(path), file=sys.stderr)
        raise
