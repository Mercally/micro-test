#!/usr/bin/env python3
"""Discover the existing laptop Docker runtime and complete its purchase deployment."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "scripts")]
from business import Lab, PREFIX, deploy, endpoint, invoke, pipe, report_dir, save
from kube import Kube, DOCKER, bootstrap, registry, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-endpoint", type=endpoint, default="http://100.100.154.68:4566")
    parser.add_argument("--database", choices=["rds", "postgres"], default="rds")
    parser.add_argument("--network")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux" or "microsoft" not in Path("/proc/sys/kernel/osrelease").read_text().lower():
        raise ValueError("Run inside laptop WSL2, with its Docker Desktop integration")
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
    if selected_env.get("LAMBDA_DOCKER_NETWORK") and selected_env["LAMBDA_DOCKER_NETWORK"] != network:
        raise ValueError("Use existing LAMBDA_DOCKER_NETWORK; do not restart/reconfigure LocalStack with unsaved data")
    clusters = json.loads(run(["k3d", "cluster", "list", "-o", "json"]).stdout)
    clusters = [c["name"] for c in clusters if c["name"].startswith(PREFIX + "eks")]
    if len(clusters) != 1: raise ValueError("Expected one owned k3d EKS cluster; inspect Managed Node Groups first")
    print(json.dumps({"localstackContainer": container["Name"].lstrip("/"), "cluster": clusters[0], "network": network,
                      "configuration": selected_env, "persistence": lab.health.get("features")}, indent=2), flush=True)
    if selected_env.get("PERSISTENCE") != "1": print("Persistence PENDIENTE. No emulator restart will be attempted.", flush=True)
    kube = Kube(clusters[0])
    bootstrap(lab, kube, argparse.Namespace(localstack_container=container["Id"], docker_network=network,
                                          database=args.database, presign_endpoint=args.client_endpoint))
    registry(lab, kube.report_dir)
    deploy(lab, argparse.Namespace(runtime=str(HERE.parent / ".state/business-runtime.json"), update=args.update))
    invoke(lab, "publisher", {"health": True})
    invoke(lab, "transport", {"operation": "health"})
    pipe(lab, "RUNNING")
    lab.client("events").enable_rule(Name=PREFIX + "publisher")
    print("Purchase flow started. Generate purchase.http from the client using business.py http.", flush=True)


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        path = report_dir() / "wsl.json"
        save(path, {"status": "FAIL", "endpoint": "http://127.0.0.1:4566", "error": str(exc)})
        print("Report: " + str(path), file=sys.stderr)
        raise
