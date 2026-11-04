#!/usr/bin/env python3
"""Real local workers: pinned Karpenter CAPI provider and isolated CAPD cluster."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit
from datetime import datetime, timezone
import uuid

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE = ROOT / ".state/karpenter-capd"
MGMT = "micro-test-local-capi-management"
CLUSTER = "micro-test-local-capd"
CONTROLLER = "micro-test-local-karpenter-controller"
OWNER = "karpenter-capd"
NODE_IMAGE = "kindest/node:v1.33.1@sha256:050072256b9a903bd914c0b2866828150cb229cea0efe5892e2b644d5dd3b34f"
CAPD_IMAGE = "micro-test-local/capd:v1.10.1-loopback"
CONTROLLER_IMAGE = "micro-test-local/karpenter-capi:v0.2.0"
SOURCES = {
    "capi": ("cluster-api", "v1.10.1", "647a1b741bc86dd759583a44b50a3ba8dbd326c4"),
    "controller": ("karpenter-provider-cluster-api", "v0.2.0", "9be2dd2dafc7b57e6bb3a8341e650a04145c3e1d"),
}
CNI_URL = "https://raw.githubusercontent.com/projectcalico/calico/v3.30.0/manifests/calico.yaml"
CNI_SHA = "cfa6f94645138e0a7e567ffb819ac8d1066734b6873c3e2a8f224b7cc812b248"


def run(*args, capture=True, timeout=1800, env=None):
    result = subprocess.run([str(x) for x in args], check=True, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            timeout=timeout, env=env)
    return result.stdout.strip() if capture else ""


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    path.chmod(0o600)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    STATE.mkdir(parents=True, exist_ok=True)
    for name, (repo, tag, commit) in SOURCES.items():
        destination = STATE / name
        if not destination.exists():
            run("git", "clone", "--depth", "1", "--branch", tag,
                "https://github.com/kubernetes-sigs/" + repo + ".git", destination, capture=False)
        if run("git", "-C", destination, "rev-parse", "HEAD") != commit:
            raise ValueError("Unexpected upstream commit: " + name)
        if run("git", "-C", destination, "status", "--porcelain"):
            raise ValueError("Preserving modified source cache: " + str(destination))
    cni = STATE / "calico.yaml"
    if not cni.exists():
        run("curl", "--fail", "--silent", "--show-error", "--proto", "=https",
            CNI_URL, "-o", cni)
    if digest(cni) != CNI_SHA:
        raise ValueError("Calico checksum mismatch; file preserved for inspection")
    print("Pinned sources and Calico checksum verified. No cluster changed.")


def validate():
    """Structural checks only; admission/CEL and reconciliation require real APIs."""
    from cfnlint.jsonschema import StandardValidator
    schemas = {}
    folders = ("capi/config/crd/bases", "capi/bootstrap/kubeadm/config/crd/bases",
               "capi/controlplane/kubeadm/config/crd/bases",
               "capi/test/infrastructure/docker/config/crd/bases", "controller/pkg/apis/crds")
    for folder in folders:
        for path in (STATE / folder).glob("*.yaml"):
            for crd in yaml.safe_load_all(path.read_text()):
                for version in crd["spec"]["versions"]:
                    key = (crd["spec"]["group"] + "/" + version["name"], crd["spec"]["names"]["kind"])
                    schemas[key] = version["schema"]["openAPIV3Schema"]
    count = 0
    for name in ("cluster.yaml", "pool.yaml"):
        for resource in yaml.safe_load_all((HERE / name).read_text()):
            if resource["kind"] == "Namespace":
                continue
            key = (resource["apiVersion"], resource["kind"])
            if key not in schemas:
                raise ValueError("Missing pinned CRD; run prepare first: " + str(key))
            errors = list(StandardValidator(schemas[key]).iter_errors(resource))
            if errors:
                raise ValueError(str(key) + ": " + "; ".join(str(e) for e in errors))
            count += 1
    print(f"PASS: {count} custom resources match pinned structural schemas. Runtime/CEL not tested.")


def kubeconfig(name, internal=False):
    args = ["kind", "get", "kubeconfig", "--name", name]
    if internal:
        args.append("--internal")
    value = yaml.safe_load(run(*args))
    for entry in value["clusters"]:
        endpoint = entry["cluster"]["server"]
        host = urlsplit(endpoint).hostname
        if host == "0.0.0.0":
            entry["cluster"]["server"] = endpoint.replace("0.0.0.0", "127.0.0.1")
    path = STATE / (name + ("-internal" if internal else "") + ".yaml")
    write_private(path, yaml.safe_dump(value))
    check_config(path, internal)
    return path


def check_config(path, internal=False):
    config = yaml.safe_load(path.read_text())
    if len(config.get("clusters", [])) != 1 or len(config.get("users", [])) != 1:
        raise ValueError("Only isolated single-cluster kubeconfigs are accepted")
    cluster = config["clusters"][0]["cluster"]
    url = urlsplit(cluster["server"])
    host = url.hostname or ""
    try:
        address = ipaddress.ip_address(host)
        allowed = address.is_loopback or (internal and address.is_private)
    except ValueError:
        allowed = internal and host in (MGMT + "-control-plane", CLUSTER + "-lb")
    if not allowed or url.scheme != "https" or cluster.get("insecure-skip-tls-verify"):
        raise ValueError("Kubernetes endpoint must be observed local and TLS verified")
    user = config["users"][0]["user"]
    if set(user) - {"client-certificate-data", "client-key-data"}:
        raise ValueError("External credential helpers/tokens are not accepted")


def kubectl(workload, *args):
    name = CLUSTER if workload else MGMT
    path = STATE / (name + ".yaml")
    check_config(path)
    timeout = "0" if args[0] in ("rollout", "wait") else "30s"
    return run("kubectl", "--kubeconfig", path, "--request-timeout=" + timeout, *args)


def get(workload, kind, name=None, namespace=None, selector=None):
    args = ["get", kind]
    if name:
        args.append(name)
    if namespace:
        args.extend(["-n", namespace])
    if selector:
        args.extend(["-l", selector])
    return json.loads(kubectl(workload, *args, "-o", "json"))


def wait_for(description, predicate, seconds=900):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        print("Waiting: " + description, flush=True)
        time.sleep(10)
    raise TimeoutError(description)


def ready(resource):
    return any(c["type"] == "Ready" and c["status"] == "True"
               for c in resource.get("status", {}).get("conditions", []))


def preflight():
    if platform.system() != "Linux" or "microsoft" not in platform.release().lower():
        raise ValueError("Runtime commands must run inside the authorized laptop WSL2")
    for command in ("docker", "kind", "kubectl", "clusterctl", "git", "curl"):
        if not shutil.which(command):
            raise ValueError("Missing command: " + command)
    # No inherited AWS identities or external kubeconfig context.
    for key in list(os.environ):
        if key.startswith(("AWS_", "CAPD_", "CAPI_", "EXP_")) or key in (
                "KUBECONFIG", "LOCALSTACK_AUTH_TOKEN", "BOTO_CONFIG", "CLUSTER_TOPOLOGY"):
            os.environ.pop(key)
    if os.environ.get("DOCKER_HOST") and not os.environ["DOCKER_HOST"].startswith("unix://"):
        raise ValueError("Remote Docker endpoints are forbidden")
    context = json.loads(run("docker", "context", "inspect"))[0]
    if not context["Endpoints"]["docker"]["Host"].startswith("unix://"):
        raise ValueError("Docker context must use the local WSL Unix socket")
    info = json.loads(run("docker", "info", "--format", "{{json .}}"))
    if info["OSType"] != "linux" or info["Architecture"] not in ("x86_64", "amd64"):
        raise ValueError("This pinned build requires Linux/amd64 Docker on the laptop")
    if "v0.29.0" not in run("kind", "version"):
        raise ValueError("Use kind v0.29.0 for this pinned lab")
    if "v1.10.1" not in run("clusterctl", "version", "-o", "short"):
        raise ValueError("Use clusterctl v1.10.1 for this pinned lab")
    sys.path.insert(0, str(ROOT / "scripts"))
    from common import Lab
    Lab("http://127.0.0.1:4566")


def receipt():
    return json.loads((STATE / "receipt.json").read_text())


def save_receipt(value):
    write_private(STATE / "receipt.json", json.dumps(value, indent=2) + "\n")


def guard():
    saved = receipt()
    if get(False, "namespace", "kube-system")["metadata"]["uid"] != saved["managementUID"]:
        raise ValueError("Management cluster identity changed; refusing mutation")
    if saved.get("workloadUID"):
        if get(True, "namespace", "kube-system")["metadata"]["uid"] != saved["workloadUID"]:
            raise ValueError("Workload cluster identity changed; refusing mutation")
    return saved


def install():
    prepare()
    manifests = {name: digest(HERE / name) for name in ("management.yaml", "cluster.yaml", "pool.yaml")}
    exists = MGMT in run("kind", "get", "clusters").splitlines()
    if exists:
        saved = guard()
        if saved["manifests"] != manifests:
            raise ValueError("Bootstrap manifests changed; preserving existing cluster")
        if saved.get("installed"):
            print("Already installed; use start/load/test/status. No replicas reset.")
            return
    else:
        if (STATE / "receipt.json").exists():
            raise ValueError("Receipt exists but management cluster is missing; inspect manually")
        if CLUSTER in run("kind", "get", "clusters").splitlines():
            raise ValueError("Workload cluster name already exists; refusing adoption")
        run("kind", "create", "cluster", "--name", MGMT, "--image", NODE_IMAGE,
            "--config", HERE / "management.yaml", "--kubeconfig", STATE / (MGMT + ".yaml"), capture=False)
        saved = {"managementUID": get(False, "namespace", "kube-system")["metadata"]["uid"],
                 "manifests": manifests}
        save_receipt(saved)
    # Build only local images; never push to a registry.
    for image, dockerfile, source in ((CAPD_IMAGE, "CAPD.Dockerfile", "capi"),
                                      (CONTROLLER_IMAGE, "Controller.Dockerfile", "controller")):
        run("docker", "build", "--platform", "linux/amd64", "-t", image,
            "-f", HERE / dockerfile, STATE / source, capture=False)
    saved["controllerImageID"] = json.loads(run("docker", "image", "inspect", CONTROLLER_IMAGE))[0]["Id"]
    save_receipt(saved)
    run("kind", "load", "docker-image", CAPD_IMAGE, "--name", MGMT, capture=False)
    # Explicit provider list: Docker only, no AWS provider and no cloud credentials.
    config = STATE / "clusterctl.yaml"
    config.write_text("providers: []\n")
    run("clusterctl", "init", "--config", config, "--kubeconfig", STATE / (MGMT + ".yaml"),
        "--core", "cluster-api:v1.10.1", "--bootstrap", "kubeadm:v1.10.1",
        "--control-plane", "kubeadm:v1.10.1", "--infrastructure", "docker:v1.10.1",
        "--wait-providers", "--wait-provider-timeout", "600", capture=False)
    kubectl(False, "patch", "deployment/capd-controller-manager", "-n", "capd-system", "--type=strategic",
            "-p", json.dumps({"spec": {"template": {"spec": {"containers": [
                {"name": "manager", "image": CAPD_IMAGE, "imagePullPolicy": "IfNotPresent"}]}}}}))
    kubectl(False, "rollout", "status", "deployment/capd-controller-manager", "-n", "capd-system", "--timeout=300s")
    # Apply this bootstrap once: reapplying replicas:0 could fight Karpenter.
    if not saved.get("clusterApplied"):
        namespaces = get(False, "namespaces")["items"]
        existing = [n for n in namespaces if n["metadata"]["name"] == CLUSTER]
        if existing and existing[0]["metadata"].get("labels", {}).get("lab.micro-test.local/owner") != OWNER:
            raise ValueError("Existing workload namespace is not owned by this lab")
        if not existing:
            kubectl(False, "create", "namespace", CLUSTER)
            kubectl(False, "label", "namespace", CLUSTER, "lab.micro-test.local/owner=" + OWNER)
        kubectl(False, "apply", "--dry-run=server", "-f", HERE / "cluster.yaml")
        kubectl(False, "apply", "-f", HERE / "cluster.yaml")
        saved["clusterApplied"] = True
        save_receipt(saved)
    wait_for("workload control plane initialized", lambda: get(False, "kubeadmcontrolplane",
             CLUSTER + "-control-plane", CLUSTER).get("status", {}).get("initialized", False))
    kubeconfig(CLUSTER)
    kubeconfig(CLUSTER, internal=True)
    kubeconfig(MGMT, internal=True)
    lb = json.loads(run("docker", "inspect", CLUSTER + "-lb"))[0]
    bindings = [b for values in lb["NetworkSettings"]["Ports"].values() for b in (values or [])]
    if not bindings or any(b["HostIp"] not in ("127.0.0.1", "::1") for b in bindings):
        raise ValueError("CAPD load balancer is not bound exclusively to loopback")
    saved["workloadUID"] = get(True, "namespace", "kube-system")["metadata"]["uid"]
    save_receipt(saved)
    kubectl(True, "apply", "--server-side", "-f", STATE / "calico.yaml")
    wait_for("workload control plane Ready", lambda: all(ready(n) for n in get(True, "nodes")["items"]) and
             bool(get(True, "nodes")["items"]))
    crds = STATE / "controller/pkg/apis/crds"
    kubectl(True, "apply", "--server-side", "-f", crds)
    for name in ("nodepools.karpenter.sh", "nodeclaims.karpenter.sh", "clusterapinodeclasses.karpenter.cluster.x-k8s.io"):
        kubectl(True, "wait", "--for=condition=Established", "crd/" + name, "--timeout=120s")
    pool = list(yaml.safe_load_all((HERE / "pool.yaml").read_text()))
    docker = json.loads(run("docker", "info", "--format", "{{json .}}"))
    pool[1]["spec"]["limits"] = {"cpu": str(3 * docker["NCPU"]), "memory": str(3 * docker["MemTotal"])}
    pool_manifest = STATE / "pool.yaml"
    pool_manifest.write_text(yaml.safe_dump_all(pool))
    kubectl(True, "apply", "--dry-run=server", "-f", pool_manifest)
    kubectl(True, "apply", "-f", pool_manifest)
    saved["installed"] = True
    save_receipt(saved)
    print("Installed. Start controller, then load and test. Runtime PASS requires test evidence.")


def controller_info():
    ids = run("docker", "ps", "-aq", "--filter", "name=^/" + CONTROLLER + "$")
    if not ids:
        return None
    info = json.loads(run("docker", "inspect", ids))[0]
    if (info["Config"].get("Labels", {}).get("lab.micro-test.local/owner") != OWNER
            or info["Image"] != receipt()["controllerImageID"]):
        raise ValueError("Refusing foreign/changed controller container")
    return info


def start():
    saved = guard()
    if not saved.get("installed"):
        raise ValueError("Run install successfully first")
    info = controller_info()
    if info:
        if not info["State"]["Running"]:
            run("docker", "start", CONTROLLER)
        print("Controller running. Logs: docker logs -f " + CONTROLLER)
        return
    # Run inside observed Docker network; no host ports or Docker socket mounted.
    mgmt = json.loads(run("docker", "inspect", MGMT + "-control-plane"))[0]
    lb = json.loads(run("docker", "inspect", CLUSTER + "-lb"))[0]
    networks = set(mgmt["NetworkSettings"]["Networks"]) & set(lb["NetworkSettings"]["Networks"])
    if len(networks) != 1:
        raise ValueError("Expected one shared observed management/workload Docker network")
    command = ["docker", "run", "-d", "--name", CONTROLLER, "--network", networks.pop(),
               "--label", "lab.micro-test.local/owner=" + OWNER, "--user", str(os.getuid()),
               "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "-e", "KUBECONFIG=/configs/workload.yaml", "-e", "SYSTEM_NAMESPACE=kube-system"]
    for name, target in ((MGMT, "management"), (CLUSTER, "workload")):
        path = STATE / (name + "-internal.yaml")
        check_config(path, internal=True)
        command.extend(["--mount", f"type=bind,source={path},target=/configs/{target}.yaml,readonly"])
    command.extend([saved["controllerImageID"], "--cluster-api-kubeconfig", "/configs/management.yaml",
                    "--leader-election-namespace", "kube-system"])
    run(*command)
    print("Controller started without published ports. Logs: docker logs -f " + CONTROLLER)


def proof():
    pods = get(True, "pods", namespace="micro-test-local-practice", selector="app=real-workers")["items"]
    if len(pods) != 2 or not all(ready(p) for p in pods):
        raise ValueError("Exactly two real Ready practice pods required")
    if len({p["spec"]["nodeName"] for p in pods}) != 2:
        raise ValueError("Practice pods must run on two different workers")
    claims = get(True, "nodeclaims", selector="karpenter.sh/nodepool=" + CLUSTER)["items"]
    machines = get(False, "machines", namespace=CLUSTER)["items"]
    results = []
    for pod in pods:
        node = get(True, "node", pod["spec"]["nodeName"])
        provider = node["spec"]["providerID"]
        claim = [x for x in claims if x.get("status", {}).get("providerID") == provider and ready(x)]
        machine = [x for x in machines if x.get("spec", {}).get("providerID") == provider]
        if not ready(node) or len(claim) != 1 or len(machine) != 1 or not provider.startswith("docker:////"):
            raise ValueError("Missing Ready NodeClaim/Node/Machine providerID correlation")
        container = provider.removeprefix("docker:////")
        info = json.loads(run("docker", "inspect", container))[0]
        if (not info["State"]["Running"] or
                info["Config"].get("Labels", {}).get("io.x-k8s.kind.cluster") != CLUSTER):
            raise ValueError("Node does not correspond to a running owned Docker container")
        marker = "real-container:" + pod["metadata"]["name"]
        output = kubectl(True, "exec", "-n", "micro-test-local-practice", pod["metadata"]["name"],
                         "--", "wget", "-qO-", "http://127.0.0.1:8080/")
        logs = kubectl(True, "logs", "-n", "micro-test-local-practice", pod["metadata"]["name"])
        if output != marker or marker not in logs:
            raise ValueError("Real HTTP/exec/log proof did not match pod identity")
        results.append({"pod": pod["metadata"]["name"], "node": node["metadata"]["name"],
                        "nodeClaim": claim[0]["metadata"]["name"], "machine": machine[0]["metadata"]["name"],
                        "providerID": provider, "dockerID": info["Id"], "http": output})
    return {"result": "PASS", "scope": "Real CAPD workers and HTTP containers; separate from purchase E2E", "workers": results}


def load():
    saved = guard()
    info = controller_info()
    if not info or not info["State"]["Running"]:
        raise ValueError("Start controller before creating pending pods")
    namespace = "micro-test-local-practice"
    existing = [n for n in get(True, "namespaces")["items"] if n["metadata"]["name"] == namespace]
    if existing:
        if existing[0]["metadata"].get("labels", {}).get("lab.micro-test.local/owner") != OWNER:
            raise ValueError("Practice namespace already exists without lab ownership")
        deployments = get(True, "deployments", namespace=namespace)["items"]
        found = [d for d in deployments if d["metadata"]["name"] == "real-workers"]
        if found:
            if found[0]["metadata"]["uid"] != saved.get("loadUID"):
                raise ValueError("Existing practice deployment has no matching receipt; preserving it")
            # Only replicas change; user edits to its pod template remain intact.
            kubectl(True, "scale", "deployment/real-workers", "-n", namespace, "--replicas=2")
            return
    else:
        kubectl(True, "create", "namespace", namespace)
        kubectl(True, "label", "namespace", namespace, "lab.micro-test.local/owner=" + OWNER)
    objects = list(yaml.safe_load_all((HERE / "load.yaml").read_text()))
    manifest = STATE / "load-deployment.yaml"
    manifest.write_text(yaml.safe_dump(objects[1]))
    kubectl(True, "create", "-f", manifest)
    saved["loadUID"] = get(True, "deployment", "real-workers", namespace)["metadata"]["uid"]
    save_receipt(saved)


def scale_down(delete_data):
    if not delete_data:
        raise ValueError("Worker deletion requires --delete-data; ephemeral worker storage will be lost")
    # Refuse draining workers that have acquired unrelated workloads.
    claims = get(True, "nodeclaims", selector="karpenter.sh/nodepool=" + CLUSTER)["items"]
    nodes = {c.get("status", {}).get("nodeName") for c in claims}
    pods = json.loads(kubectl(True, "get", "pods", "-A", "-o", "json"))["items"]
    for pod in pods:
        if pod["spec"].get("nodeName") not in nodes:
            continue
        namespace = pod["metadata"]["namespace"]
        if namespace not in ("kube-system", "micro-test-local-practice"):
            raise ValueError("Worker hosts another workload; preserving it: " + namespace)
        if any("persistentVolumeClaim" in v for v in pod["spec"].get("volumes", [])):
            raise ValueError("Worker hosts persistent storage; inspect and drain manually")
        if namespace == "kube-system" and not pod["metadata"]["name"].startswith((
                "calico-node-", "calico-kube-controllers-", "coredns-", "kube-proxy-")):
            raise ValueError("Unknown system pod on worker; refusing drain")
        if namespace == "micro-test-local-practice" and pod["metadata"].get("labels", {}).get("app") != "real-workers":
            raise ValueError("Foreign practice pod found; refusing drain")
    kubectl(True, "scale", "deployment/real-workers", "-n", "micro-test-local-practice", "--replicas=0")
    wait_for("practice pods terminated", lambda: not get(True, "pods", namespace="micro-test-local-practice",
             selector="app=real-workers")["items"], 180)
    removed = []
    for claim in claims:
        provider = claim.get("status", {}).get("providerID", "")
        if not provider.startswith("docker:////"):
            raise ValueError("Uncorrelated NodeClaim; inspect manually")
        container = provider.removeprefix("docker:////")
        info = json.loads(run("docker", "inspect", container))[0]
        if info["Config"].get("Labels", {}).get("io.x-k8s.kind.cluster") != CLUSTER:
            raise ValueError("Refusing foreign worker")
        kubectl(True, "delete", "nodeclaim", claim["metadata"]["name"], "--wait=false")
        wait_for("Docker worker removed by CAPD: " + container,
                 lambda: not run("docker", "ps", "-aq", "--filter", "id=" + info["Id"]))
        wait_for("NodeClaim, Node and Machine removed: " + container,
                 lambda: not any(c.get("status", {}).get("providerID") == provider
                                 for c in get(True, "nodeclaims")["items"])
                 and not any(n.get("spec", {}).get("providerID") == provider
                             for n in get(True, "nodes")["items"])
                 and not any(m.get("spec", {}).get("providerID") == provider
                             for m in get(False, "machines", namespace=CLUSTER)["items"]))
        removed.append(info["Id"])
    if not removed:
        raise ValueError("No workers were removed; scale-down proof requires existing workers")
    md = get(False, "machinedeployment", CLUSTER + "-workers", CLUSTER)
    if md["spec"]["replicas"] != 0:
        raise ValueError("Worker replicas did not return to zero")
    return {"result": "PASS", "scope": "Explicit NodeClaim deletion removed real Docker workers", "removed": removed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "validate", "install", "start", "load", "test", "status", "scale-down", "stop"))
    parser.add_argument("--delete-data", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
        return
    if args.command == "validate":
        validate()
        return
    report = {"command": args.command, "result": "FAIL", "target": "laptop WSL2 / local Docker only"}
    path = ROOT / "reports/laptop" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]) / "karpenter.json"
    try:
        preflight()
        if args.command == "install":
            install()
        else:
            guard()
            if args.command == "start":
                start()
            elif args.command == "load":
                report["before"] = {"nodeClaims": get(True, "nodeclaims"),
                                    "workers": get(False, "machinedeployment", CLUSTER + "-workers", CLUSTER)}
                load()
            elif args.command == "test":
                kubectl(True, "rollout", "status", "deployment/real-workers", "-n", "micro-test-local-practice", "--timeout=900s")
                report.update(proof())
            elif args.command == "scale-down":
                report.update(scale_down(args.delete_data))
            elif args.command == "stop":
                if controller_info():
                    run("docker", "stop", CONTROLLER)
            else:
                report.update(nodes=get(True, "nodes"), nodeClaims=get(True, "nodeclaims"),
                              machines=get(False, "machines", namespace=CLUSTER),
                              machineDeployments=get(False, "machinedeployments", namespace=CLUSTER))
        if report["result"] == "FAIL":
            report["result"] = "COMMAND_OK"
            report["note"] = "Command completed; this is not proof of runtime acceptance"
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n")
        print("Report: " + str(path))


if __name__ == "__main__":
    main()
