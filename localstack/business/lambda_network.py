"""Use the existing Lambda network for local NodePort and LocalStack transport."""
import ipaddress
import json
from pathlib import Path

from business import PREFIX, endpoint, save
from kube import DOCKER, current_node_containers, nodeport_target, run


def private_ipv4(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or not any(address in ipaddress.ip_network(net) for net in
            ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise ValueError("Expected an observed private Docker IPv4 address")
    return str(address)


def lambda_network(container, cluster_network):
    config = container["Config"]
    if not config["Image"].startswith("localstack/localstack"):
        raise ValueError("Selected container is not LocalStack")
    env = dict(item.split("=", 1) for item in config.get("Env", []) if "=" in item)
    if any(env.get(key) for key in ("LAMBDA_DOCKER_FLAGS", "LOCALSTACK_LAMBDA_DOCKER_FLAGS")):
        raise ValueError("Custom Lambda Docker flags require inspecting their networking before repair")
    if any(env.get(key) not in (None, "", "0") for key in ("ENABLE_CONFIG_UPDATES", "LOCALSTACK_ENABLE_CONFIG_UPDATES")):
        raise ValueError("Dynamic LocalStack config is enabled; container environment alone cannot establish Lambda network")
    values = {env[key] for key in ("LAMBDA_DOCKER_NETWORK", "LOCALSTACK_LAMBDA_DOCKER_NETWORK") if env.get(key)}
    if len(values) > 1:
        raise ValueError("Conflicting Lambda network configuration")
    network = next(iter(values), "bridge")
    if network not in ("bridge", cluster_network):
        raise ValueError("Repair supports only existing bridge or selected lab k3d network")
    address = container["NetworkSettings"]["Networks"].get(network, {}).get("IPAddress")
    if not address:
        raise ValueError("LocalStack is not attached to its Lambda network")
    return network, private_ipv4(address)


def prepare(lab, kube, localstack_id, cluster_network, runtime_path):
    """Attach one owned Ready node if needed; preserve kube routes and save reviewed endpoints."""
    if endpoint(lab.endpoint) != "http://127.0.0.1:4566":
        raise ValueError("Run network repair inside laptop WSL")
    runtime_path = Path(runtime_path)
    runtime = json.loads(runtime_path.read_text())
    endpoint(runtime["endpoint"])
    mode = runtime.get("databaseMode")
    if mode not in ("rds", "postgres"):
        raise ValueError("Missing/unknown database mode; do not guess or replace the database")
    container = json.loads(run(DOCKER + ["inspect", localstack_id]).stdout)[0]
    network, localstack_ip = lambda_network(container, cluster_network)
    info = json.loads(run(DOCKER + ["network", "inspect", network]).stdout)[0]
    if info.get("Name") != network or info.get("Driver") != "bridge" or info.get("Scope") != "local":
        raise ValueError("Expected existing local Docker bridge network")
    # Validate the existing database before any Docker or runtime-file changes.
    if mode == "rds":
        db = lab.client("rds").describe_db_instances(DBInstanceIdentifier=PREFIX + "database")["DBInstances"][0]
        if db["DBInstanceStatus"] != "available" or int(runtime["DatabasePort"]) != db["Endpoint"]["Port"]:
            raise ValueError("Existing runtime does not match available RDS port; preserve database configuration")
        addresses = {item.get("IPAddress") for item in container["NetworkSettings"]["Networks"].values()}
        if runtime["DatabaseHost"] not in addresses:
            raise ValueError("Existing RDS endpoint is not an observed address of this LocalStack container")
    elif int(runtime["DatabasePort"]) != 31543:
        raise ValueError("Expected existing PostgreSQL fallback NodePort 31543")
    # Prefer an already attached Ready node. Never modify Lambda's ephemeral containers.
    nodes = current_node_containers(kube.cluster)
    if any(network in c["NetworkSettings"]["Networks"] for c in nodes):
        target = nodeport_target(kube, network)
    else:
        target = nodeport_target(kube, cluster_network)
        selected = json.loads(run(DOCKER + ["inspect", target["containerID"]]).stdout)[0]
        labels = selected["Config"].get("Labels", {})
        if (labels.get("k3d.cluster") != kube.cluster or labels.get("k3d.role") not in ("server", "agent")
                or not selected["State"]["Running"]
                or cluster_network not in selected["NetworkSettings"]["Networks"]):
            raise ValueError("Selected Ready node identity changed; rerun inventory")
        if "--gw-priority" not in run(DOCKER + ["network", "connect", "--help"]).stdout:
            raise ValueError("Docker needs network connect --gw-priority support to preserve node default route")
        save(kube.report_dir / "business-runtime-before-network.json", runtime)
        print("Attaching owned Ready node to Lambda network " + network + ": " + target["node"], flush=True)
        run(DOCKER + ["network", "connect", "--gw-priority", "-1", network, target["containerID"]])
        target = nodeport_target(kube, network)
    node_ip = private_ipv4(target["ip"])
    updated = runtime | {"RuntimeEndpoint": f"http://{localstack_ip}:4566",
                         "MicroBaseUrl": f"http://{node_ip}:31080",
                         "DatabaseHost": localstack_ip if mode == "rds" else node_ip}
    backup = kube.report_dir / "business-runtime-before-network.json"
    if not backup.exists():
        save(backup, runtime)
    save(runtime_path, updated)
    result = {"status": "PREPARED", "lambdaNetwork": network, "nodePortTarget": target,
              "runtime": updated, "note": "Runtime health and end-to-end acceptance still required; no emulator restart."}
    save(kube.report_dir / "lambda-network-prepared.json", result)
    print(json.dumps(result, indent=2), flush=True)
    return result
