#!/usr/bin/env python3
"""WSL-only bootstrap. No inherited Docker context or Kubernetes credentials."""
import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import urlsplit

import yaml
from common import ROOT, PREFIX, REGION, ACCOUNT, Lab, endpoint_url, save

NS = PREFIX + "general"
ISTIO = "1.31.3"
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
MANIFESTS = ROOT / "kubernetes"


def verify_cluster_config(cluster, config, expected_network=None, expected_api_port=None):
    legacy = cluster.startswith(PREFIX + "eks")
    generated = re.fullmatch(re.escape(PREFIX) + r"[0-9a-f]{8}", cluster) is not None
    if not legacy and not (generated and expected_network == "k3d-" + cluster and expected_api_port):
        raise ValueError("Generated k3d names require verified Docker network and API port")
    if len(config.get("clusters", [])) != 1 or len(config.get("users", [])) != 1:
        raise ValueError("Expected isolated single-cluster k3d kubeconfig")
    value = config["clusters"][0]["cluster"]
    if not base64.b64decode(value.get("certificate-authority-data", ""), validate=True).strip():
        raise ValueError("Internal k3d certificate authority is required for TLS verification")
    if any(key in value for key in ("proxy-url", "tls-server-name", "certificate-authority")):
        raise ValueError("Proxy, alternate TLS name and external CA paths are not accepted")
    server = urlsplit(value["server"])
    if (server.scheme != "https" or server.hostname not in ("127.0.0.1", "localhost", "0.0.0.0")
            or not server.port or server.username or server.password or server.query or server.fragment
            or server.path not in ("", "/")):
        raise ValueError("Generated k3d kubeconfig is not a loopback TLS endpoint")
    if expected_api_port is not None and server.port != expected_api_port:
        raise ValueError("k3d kubeconfig port differs from observed Docker load balancer API port")
    value["server"] = f"https://127.0.0.1:{server.port}"
    if value.get("insecure-skip-tls-verify"):
        raise ValueError("Kubernetes TLS verification required")
    if set(config["users"][0]["user"]) != {"client-certificate-data", "client-key-data"}:
        raise ValueError("Use only local k3d certificate credentials")


def run(cmd, input=None, check=True):
    result = subprocess.run(cmd, input=input, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if check and result.returncode:
        raise RuntimeError(f"{cmd[0]} failed: {result.stderr.strip()}")
    return result


def current_node_containers(cluster, roles=("server", "agent")):
    """Read current node IDs; k3d helpers/LB are not Kubernetes nodes."""
    for attempt in range(3):
        ids = []
        for role in roles:
            ids.extend(run(DOCKER + ["ps", "-q", "--no-trunc", "--filter", "label=k3d.cluster=" + cluster,
                                    "--filter", "label=k3d.role=" + role]).stdout.split())
        if not ids:
            raise ValueError("No running Docker nodes for selected k3d cluster")
        nodes = []
        for cid in sorted(set(ids)):
            result = run(DOCKER + ["inspect", cid], check=False)
            if result.returncode:
                message = result.stderr.strip()
                if "no such object:" in message.lower() or "no such container:" in message.lower():
                    # Bounded retry of a new inventory, never reuse a stale ID list.
                    break
                raise RuntimeError("docker inspect failed: " + message)
            node = json.loads(result.stdout)[0]
            labels = node["Config"].get("Labels", {})
            if labels.get("k3d.cluster") != cluster or labels.get("k3d.role") not in roles:
                raise ValueError("Docker node identity changed during discovery")
            if not node["State"]["Running"]:
                break
            nodes.append(node)
        else:
            return nodes
    raise RuntimeError("Docker nodes changed during three discovery attempts; inspect cluster reconciliation")


def nodeport_target(kube, network):
    """Map a currently Ready Kubernetes node to a current private Docker address."""
    nodes = json.loads(kube.call("get", "nodes", "-o", "json", "--request-timeout=30s").stdout)["items"]
    ready_names = {n["metadata"]["name"] for n in nodes
                   if any(c["type"] == "Ready" and c["status"] == "True"
                          for c in n.get("status", {}).get("conditions", []))}
    for container in current_node_containers(kube.cluster):
        names = (container["Name"].lstrip("/"), container["Config"].get("Hostname"))
        name = next((n for n in names if n in ready_names), None)
        if not name:
            continue
        address = container["NetworkSettings"]["Networks"].get(network, {}).get("IPAddress", "")
        if not address:
            continue
        parsed = ipaddress.ip_address(address)
        if parsed.version != 4 or not parsed.is_private or parsed.is_loopback or parsed.is_unspecified:
            raise ValueError("NodePort target must be an observed private Docker IPv4 address")
        return {"node": name, "containerID": container["Id"], "ip": address, "network": network}
    raise ValueError("Cannot map a currently Ready Kubernetes node to the selected Docker network; no serverlb fallback")


class Kube:
    def __init__(self, cluster, expected_network=None, expected_api_port=None):
        if sys.platform != "linux" or "microsoft" not in Path("/proc/sys/kernel/osrelease").read_text().lower():
            raise ValueError("Run this command in PC/WSL2; gateway 4566 does not provide Docker/Kubernetes access")
        os.environ.pop("DOCKER_CONTEXT", None)
        os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
        self.cluster = cluster
        self.report_dir = ROOT / "reports/laptop" / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-kube-" + uuid.uuid4().hex[:6])
        info = json.loads(run(DOCKER + ["info", "--format", "{{json .}}"] ).stdout)
        if info.get("OSType") != "linux": raise ValueError("Linux Docker daemon required")
        config = yaml.safe_load(run(["k3d", "kubeconfig", "get", cluster]).stdout)
        verify_cluster_config(cluster, config, expected_network, expected_api_port)
        path = ROOT / ".state/kubeconfig"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(config)); path.chmod(0o600)
        self.base = ["kubectl", "--kubeconfig", str(path)]
        self.path = path

    def call(self, *args, input=None, check=True):
        return run(self.base + list(args), input, check)

    def apply(self, name):
        self.call("apply", "-f", str(MANIFESTS / name))

    def apply_data(self, value):
        self.call("apply", "-f", "-", input=yaml.safe_dump(value))

    def inspect(self):
        nodes = json.loads(self.call("get", "nodes", "-o", "json", "--request-timeout=30s").stdout)["items"]
        version = json.loads(self.call("version", "-o", "json", "--request-timeout=30s").stdout)
        return {"version": version, "nodes": [
            {"name": n["metadata"]["name"], "labRole": n["metadata"].get("labels", {}).get("lab-role"),
             "lab": n["metadata"].get("labels", {}).get("lab"), "taints": n["spec"].get("taints", []),
             "ready": any(c["type"] == "Ready" and c["status"] == "True" for c in n.get("status", {}).get("conditions", [])),
             "kubeletVersion": n.get("status", {}).get("nodeInfo", {}).get("kubeletVersion")}
            for n in nodes]}

    def ready(self):
        self.call("wait", "--for=condition=Ready", "nodes", "--all", "--timeout=180s")
        nodes = json.loads(self.call("get", "nodes", "-o", "json").stdout)["items"]
        for role in ("general", "terceros", "onprem"):
            workers = [n for n in nodes if n["metadata"].get("labels", {}).get("lab-role") == role]
            if not workers: raise ValueError(f"No actual Ready worker with lab-role={role}; API status is insufficient")
            if role != "general" and not all(any(t.get("key") == "dedicated" and t.get("value") == role and t.get("effect") == "NoSchedule" for t in n["spec"].get("taints", [])) for n in workers):
                raise ValueError(f"Nodegroup taints not applied for {role}")
        version = json.loads(self.call("version", "-o", "json").stdout)["serverVersion"]["gitVersion"]
        minor = int(version.split(".")[1])
        if not 32 <= minor <= 37: raise ValueError(f"Istio {ISTIO} does not support observed Kubernetes {version}")
        return {"kubernetes":version, "nodes":[{"name":n["metadata"]["name"], "role":n["metadata"].get("labels", {}).get("lab-role")} for n in nodes]}


def bootstrap(lab, kube, args):
    proof = kube.ready()
    version = json.loads(run(["istioctl", "version", "--remote=false", "-o", "json"]).stdout)["clientVersion"]["version"]
    if version != ISTIO: raise ValueError(f"Install istioctl {ISTIO}; observed {version}")
    print("Installing pinned Istio into this dedicated local cluster", flush=True)
    run(["istioctl", "--kubeconfig", str(kube.path), "install", "--set", "profile=minimal", "--set", "values.pilot.resources.requests.cpu=100m", "-y"])
    kube.call("-n", "istio-system", "rollout", "status", "deployment/istiod", "--timeout=180s")
    kube.apply("01-namespaces.yaml")
    container = json.loads(run(DOCKER + ["inspect", args.localstack_container]).stdout)[0]
    if not container["Config"]["Image"].startswith("localstack/localstack"):
        raise ValueError("Selected container is not a LocalStack image")
    networks = container["NetworkSettings"]["Networks"]
    if args.docker_network not in networks: raise ValueError("LocalStack is not attached to requested Docker network")
    address = networks[args.docker_network]["IPAddress"]
    if not ipaddress.ip_address(address).is_private: raise ValueError("Expected private Docker network address")
    for info in current_node_containers(kube.cluster):
        if args.docker_network not in info["NetworkSettings"]["Networks"]:
            run(DOCKER + ["network", "connect", args.docker_network, info["Id"]])
    print("Building existing micro Dockerfile and importing local image", flush=True)
    run(DOCKER + ["build", "-t", "docs-simulator:local", str(ROOT.parent)])
    run(["k3d", "image", "import", "--cluster", kube.cluster, "docs-simulator:local"])
    if args.database == "rds":
        db = lab.client("rds").describe_db_instances(DBInstanceIdentifier=PREFIX + "database")["DBInstances"][0]
        if db["DBInstanceStatus"] != "available":
            raise ValueError("RDS runtime unavailable. Investigate on PC, or explicitly rerun --database postgres (different local backend)")
        port = db["Endpoint"]["Port"]
        host = address
        proof["rdsEndpoint"] = db["Endpoint"]
        proof["podDatabaseEndpoint"] = {"host":host,"port":port,"note":"Docker transport to LocalStack port; no AWS DNS/SG parity"}
    else:
        kube.apply("03-postgres-fallback.yaml")
        kube.call("-n", NS, "rollout", "status", "statefulset/micro-test-local-postgres", "--timeout=240s")
        host, port = "micro-test-local-postgres", 5432
    config = {"SERVER_PORT":"8080", "DATABASE_URL":f"jdbc:postgresql://{host}:{port}/docs",
              "AWS_REGION":"us-east-1", "AWS_ACCESS_KEY_ID":"test", "AWS_SECRET_ACCESS_KEY":"test",
              "AWS_EC2_METADATA_DISABLED":"true", "S3_ENDPOINT_URL":f"http://{address}:4566",
              "S3_PRESIGN_ENDPOINT_URL":endpoint_url(args.presign_endpoint), "S3_BUCKET":lab.outputs("storage")["BucketName"],
              "SIMULATOR_FAILURES_BEFORE_SUCCESS":"0", "SIMULATOR_FAILURE_PROBABILITY":"0",
              "LAB_PURCHASES_ENABLED":"true",
              "OTEL_SDK_DISABLED":"true", "JAVA_TOOL_OPTIONS":"-XX:MaxRAMPercentage=65"}
    kube.apply_data({"apiVersion":"v1","kind":"ConfigMap","metadata":{"name":PREFIX + "config","namespace":NS},"data":config})
    kube.apply_data({"apiVersion":"v1","kind":"Secret","metadata":{"name":PREFIX + "database","namespace":NS},"type":"Opaque","stringData":{"DATABASE_USERNAME":"docs","DATABASE_PASSWORD":"local-docs-only"}})
    # PostgreSQL query runs from the same cluster transport used by the application.
    job = PREFIX + "sql-check"
    kube.call("-n", NS, "delete", "job", job, "--ignore-not-found=true")
    kube.apply_data({"apiVersion":"batch/v1","kind":"Job","metadata":{"name":job,"namespace":NS},"spec":{"backoffLimit":0,"template":{"metadata":{"annotations":{"sidecar.istio.io/inject":"false"}},"spec":{"restartPolicy":"Never","nodeSelector":{"lab-role":"general"},"containers":[{"name":"sql","image":"postgres:16.13-alpine","env":[{"name":"PGPASSWORD","value":"local-docs-only"}],"command":["psql",f"host={host} port={port} dbname=docs user=docs connect_timeout=5","-v","ON_ERROR_STOP=1","-c","SELECT current_database(), current_user, version();"]}]}}}})
    kube.call("-n",NS,"wait","--for=condition=complete","job/"+job,"--timeout=180s")
    proof["sql"] = kube.call("-n",NS,"logs","job/"+job).stdout.strip()
    kube.apply("02-micro.yaml")
    kube.call("-n", NS, "rollout", "restart", "deployment/micro-test-local-micro")
    kube.call("-n", NS, "rollout", "status", "deployment/micro-test-local-micro", "--timeout=300s")
    kube.apply("04-gateway.yaml")
    kube.call("-n", NS, "rollout", "status", "deployment/micro-test-local-gateway", "--timeout=180s")
    kube.apply("09-scheduling.yaml")
    for role in ("general","terceros","onprem"):
        kube.call("-n", PREFIX+role, "wait", "--for=condition=Ready", "pod/micro-test-local-scheduling", "--timeout=180s")
    security(kube)
    # Build/import and probes can outlive/recreate Docker containers. Rediscover now.
    print("Resolving current Ready node for Lambda/NodePort transport", flush=True)
    proof["nodePortTarget"] = nodeport_target(kube, args.docker_network)
    node_ip = proof["nodePortTarget"]["ip"]
    save(ROOT / ".state/micro-api-parameters.json", {"MicroBaseUrl":f"http://{node_ip}:31080"})
    publisher_host, publisher_port = host, port
    if args.database == "postgres":
        # Explicit fallback only: Lambda cannot resolve the Kubernetes service DNS.
        kube.apply_data({"apiVersion":"v1","kind":"Service", "metadata":{"name":PREFIX+"postgres-publisher","namespace":NS},
                         "spec":{"type":"NodePort","selector":{"app":PREFIX+"postgres"},
                                 "ports":[{"port":5432,"targetPort":5432,"nodePort":31543}]}})
        publisher_host, publisher_port = node_ip, 31543
    save(ROOT / ".state/business-runtime.json", {"MicroBaseUrl":f"http://{node_ip}:31080",
         "RuntimeEndpoint":f"http://{address}:4566", "DatabaseHost":publisher_host,
         "DatabasePort":str(publisher_port), "endpoint":lab.endpoint, "databaseMode":args.database})
    proof.update(istio=ISTIO,databaseMode=args.database,status="PASS")
    proof["endpoint"] = lab.endpoint
    save(kube.report_dir / "kubernetes.json", proof)
    print("Reports: " + str(kube.report_dir), flush=True)
    print(json.dumps(proof,indent=2))


def security(kube):
    # Check actual K3s policy engine, not merely the existence of NetworkPolicy objects.
    servers = []
    for info in current_node_containers(kube.cluster, roles=("server",)):
        command = " ".join(info["Config"].get("Cmd") or [])
        if "server" in command:
            if "--disable-network-policy" in command:
                raise ValueError("K3s network policy controller is disabled; no policies applied")
            servers.append(info["Id"])
    if not servers: raise ValueError("Cannot verify embedded K3s/kube-router network policy controller")
    kube.apply("08-probes.yaml")
    kube.call("-n",NS,"wait","--for=condition=Ready","pod","-l","lab-probe=true","--timeout=180s")
    # Baselines prevent a DNS, crashed pod, or broad network failure from counting as denial.
    kube.call("-n",NS,"delete","networkpolicy",PREFIX+"micro","--ignore-not-found=true")
    kube.call("-n",NS,"delete","authorizationpolicy",PREFIX+"micro","--ignore-not-found=true")
    kube.apply_data({"apiVersion":"security.istio.io/v1","kind":"PeerAuthentication","metadata":{"name":PREFIX+"strict","namespace":NS},"spec":{"selector":{"matchLabels":{"app":PREFIX+"micro"}},"mtls":{"mode":"PERMISSIVE"}}})
    kube.call("-n",NS,"label","pod",PREFIX+"probe-blocked","lab-access=allowed","--overwrite")
    url = "http://micro-test-local-micro:8080/actuator/health"
    def curl(role):
        return kube.call("-n",NS,"exec",PREFIX+"probe-"+role,"-c","curl","--","curl","--max-time","5","-sS","-o","/dev/null","-w","%{http_code}","-H","Connection: close",url,check=False)
    def expect(role,code=None,network=False):
        for attempt in range(15):
            r=curl(role)
            if code and r.returncode == 0 and r.stdout == code: return
            if code is None and r.returncode != 0 and r.stdout == "000": return
            if network and r.returncode == 0 and r.stdout == "503": return
            time.sleep(2)
        raise AssertionError(f"{role}: expected {code or 'connection rejection'}, got {r.returncode}/{r.stdout}")
    try:
        for role in ("plain","allowed","denied","blocked"): expect(role,"200")
        kube.apply("05-mtls.yaml")
        expect("allowed","200"); expect("plain")
        kube.apply("06-authorization.yaml")
        expect("allowed","200"); expect("denied","403"); expect("blocked","200")
        kube.apply("07-network-policy.yaml")
        kube.call("-n",NS,"label","pod",PREFIX+"probe-blocked","lab-access=blocked","--overwrite")
        expect("allowed","200"); expect("blocked",network=True)
        kube.call("-n",NS,"label","pod",PREFIX+"probe-blocked","lab-access=allowed","--overwrite")
        expect("blocked","200")
        kube.call("-n",NS,"label","pod",PREFIX+"probe-blocked","lab-access=blocked","--overwrite")
        save(kube.report_dir/"mesh.json",{"status":"PASS","checks":["plaintext baseline","STRICT rejects plaintext","mesh accepts mTLS","unauthorized service account receives 403","network deny blocks authorized mesh identity","network allow restores connectivity"],"policyEngine":"K3s embedded kube-router with flannel"})
    finally:
        # Restore restrictive state even when a diagnostic fails.
        kube.apply("05-mtls.yaml"); kube.apply("06-authorization.yaml"); kube.apply("07-network-policy.yaml")


def registry_target(uri, proxy_endpoint):
    # The hostname routes /v2/ to ECR. Replacing it with 127.0.0.1 yields 404.
    host = f"{ACCOUNT}.dkr.ecr.{REGION}.localhost.localstack.cloud:4566"
    if uri != host + "/" + PREFIX + "docs":
        raise ValueError("ECR registry must match the verified local account, region and repository")
    proxy = urlsplit(proxy_endpoint)
    if (proxy.scheme not in ("http", "https") or proxy.netloc != host
            or proxy.path not in ("", "/") or proxy.query or proxy.fragment):
        raise ValueError("ECR authorization endpoint does not match the local repository")
    return host


def registry(lab, report_dir=None):
    if (sys.platform != "linux" or "microsoft" not in Path("/proc/sys/kernel/osrelease").read_text().lower()
            or lab.endpoint != "http://127.0.0.1:4566"):
        raise ValueError("Registry proof must run in laptop WSL using http://127.0.0.1:4566")
    uri = lab.client("ecr").describe_repositories(repositoryNames=[PREFIX+"docs"])["repositories"][0]["repositoryUri"]
    authorization = lab.client("ecr").get_authorization_token()["authorizationData"][0]
    registry_host = registry_target(uri, authorization["proxyEndpoint"])
    addresses = socket.getaddrinfo(registry_host.rsplit(":", 1)[0], 4566, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_loopback for a in addresses):
        raise ValueError("Local ECR hostname must resolve only to loopback in WSL; inspect DNS before retrying")
    username,password = base64.b64decode(authorization["authorizationToken"], validate=True).decode().split(":",1)
    parent = ROOT / ".state"
    parent.mkdir(parents=True, exist_ok=True)
    print("Testing local ECR registry: " + registry_host, flush=True)
    # Isolated credentials; never delete a user's existing Docker config directory.
    with tempfile.TemporaryDirectory(prefix="docker-registry-", dir=parent) as directory:
        docker=DOCKER+["--config",directory]
        try:
            run(docker+["login",registry_host,"--username",username,"--password-stdin"],input=password)
            run(docker+["tag","docs-simulator:local",uri+":lab"])
            run(docker+["push",uri+":lab"])
            digest=lab.client("ecr").describe_images(repositoryName=PREFIX+"docs",imageIds=[{"imageTag":"lab"}])["imageDetails"][0]["imageDigest"]
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError("ECR returned an invalid image digest")
            run(docker+["pull",uri+"@"+digest])
            report = report_dir or ROOT / "reports/laptop" / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-ecr-" + uuid.uuid4().hex[:6])
            save(report/"ecr.json",{"status":"PASS","endpoint":lab.endpoint,"digest":digest,"transport":uri,"note":"Push/pull on laptop. Does not test EKS kubelet IAM or registry auth."})
            print("PASS: local ECR push and pull by digest",digest)
            print("Report: " + str(report / "ecr.json"))
        finally:
            run(docker+["logout",registry_host],check=False)


def stop(kube):
    for kind,name in [("deployment",PREFIX+"micro"),("deployment",PREFIX+"gateway"),("statefulset",PREFIX+"postgres")]:
        exists=kube.call("-n",NS,"get",kind,name,"--ignore-not-found=true","-o","name").stdout.strip()
        if exists:kube.call("-n",NS,"scale",exists,"--replicas=0")
    for role in ("general","terceros","onprem"):
        kube.call("-n",PREFIX+role,"delete","pod",PREFIX+"scheduling","--ignore-not-found=true")
    kube.call("-n",NS,"delete","pod","-l","lab-probe=true")
    print("Stopped lab workloads. Namespaces, PVCs, nodes and Istio retained.")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--endpoint",required=True,type=endpoint_url)
    p.add_argument("--k3d-cluster",required=True)
    p.add_argument("command",choices=["bootstrap","ready","security","registry","stop"])
    p.add_argument("--localstack-container")
    p.add_argument("--docker-network",default="micro-test-lab")
    p.add_argument("--database",choices=["rds","postgres"],default="rds")
    p.add_argument("--presign-endpoint",type=endpoint_url)
    a=p.parse_args()
    lab=Lab(a.endpoint)
    if urlsplit(lab.endpoint).hostname not in ("127.0.0.1","localhost","::1"):
        raise ValueError("WSL bootstrap requires its loopback LocalStack endpoint")
    if a.command=="registry":
        registry(lab)
        return
    kube=Kube(a.k3d_cluster)
    if a.command=="bootstrap":
        if not a.localstack_container or not a.presign_endpoint: raise ValueError("--localstack-container and --presign-endpoint required")
        bootstrap(lab,kube,a)
    elif a.command=="ready": print(json.dumps(kube.ready(),indent=2))
    elif a.command=="security": security(kube)
    else: stop(kube)


if __name__=="__main__":
    try: main()
    except Exception as exc:
        print(f"FAIL: {exc}",file=sys.stderr)
        sys.exit(1)
