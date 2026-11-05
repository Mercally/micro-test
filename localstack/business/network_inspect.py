#!/usr/bin/env python3
"""Inspect laptop Lambda networking without changing Docker or LocalStack configuration."""
import json
import os
from pathlib import Path
import subprocess
import sys

from business import Lab, PREFIX, invoke, report_dir, save

FUNCTIONS = {PREFIX + suffix for suffix in ("publisher", "transport", "authorizer")}
CONFIG_KEYS = {"LAMBDA_DOCKER_NETWORK", "ENABLE_CONFIG_UPDATES", "PERSISTENCE"}
RUNTIME_KEYS = {"LAB_AWS_ENDPOINT", "MICRO_BASE_URL", "DATABASE_HOST", "DATABASE_PORT"}


def summarize(container):
    """Return only lab identities, network addresses and explicitly allowed configuration."""
    config = container.get("Config", {})
    env = dict(item.split("=", 1) for item in config.get("Env", []) if "=" in item)
    function = env.get("AWS_LAMBDA_FUNCTION_NAME")
    image = config.get("Image", "")
    localstack = image.startswith(("localstack/localstack:", "localstack/localstack-pro:"))
    localstack = localstack or image in {"localstack/localstack", "localstack/localstack-pro"}
    cluster = config.get("Labels", {}).get("k3d.cluster", "")
    if function not in FUNCTIONS and not localstack and not cluster.startswith("micro-test-local-"):
        return None
    keys = CONFIG_KEYS if localstack else RUNTIME_KEYS if function in FUNCTIONS else set()
    return {
        "name": container.get("Name", "").lstrip("/"),
        "function": function if function in FUNCTIONS else None,
        "cluster": cluster or None,
        "running": container.get("State", {}).get("Running", False),
        "networkMode": container.get("HostConfig", {}).get("NetworkMode"),
        "networks": {name: {"ipv4": details.get("IPAddress"), "networkId": details.get("NetworkID")}
                     for name, details in container.get("NetworkSettings", {}).get("Networks", {}).items()},
        "configuration": {key: env[key] for key in sorted(keys) if key in env},
    }


def main():
    if sys.platform != "linux" or "microsoft" not in Path("/proc/sys/kernel/osrelease").read_text().lower():
        raise ValueError("Run inside laptop WSL2 with Docker Desktop integration")
    docker_env = dict(os.environ, DOCKER_HOST="unix:///var/run/docker.sock")
    docker_env.pop("DOCKER_CONTEXT", None)
    lab = Lab("http://127.0.0.1:4566")
    health = {}
    # Read-only function operations also keep these runtimes available for Docker inspection.
    for suffix, payload in (("publisher", {"health": True}), ("transport", {"operation": "health"})):
        print("Checking " + suffix + " health...", flush=True)
        try:
            health[suffix] = {"status": "PASS", "result": invoke(lab, suffix, payload)}
        except Exception as exc:
            # Full Lambda errors may contain application data; keep only exception class here.
            health[suffix] = {"status": "FAIL", "errorType": type(exc).__name__}
    def docker(*args):
        return subprocess.check_output(["docker", *args], env=docker_env, text=True, timeout=30)
    containers = []
    for cid in docker("ps", "-q").split():
        try:
            value = summarize(json.loads(docker("inspect", cid))[0])
        except subprocess.CalledProcessError:
            raise RuntimeError("Docker container changed during inspection; rerun this command") from None
        if value:
            containers.append(value)
    observed = {c["function"] for c in containers if c["function"]}
    result = {"status": "INSPECTED", "endpoint": lab.endpoint, "health": health,
              "containers": containers, "missingFunctionContainers": sorted(FUNCTIONS - observed),
              "note": "No network changes, restarts, purchase requests or trigger activation. Missing runtimes may be idle."}
    path = report_dir() / "lambda-network.json"
    save(path, result)
    print(json.dumps(result, indent=2))
    print("Report: " + str(path))


if __name__ == "__main__":
    main()
