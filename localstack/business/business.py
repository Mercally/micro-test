#!/usr/bin/env python3
"""Laptop-only purchase lab. Explicit endpoints, ownership checks, preserved data."""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
from common import Lab, PREFIX, ROOT, TAG, NoRedirect, save
from lab import wait_stack
import yaml

STATE = ROOT / ".state/business"
PIPE = PREFIX + "purchases"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def endpoint(value):
    if value.rstrip("/") not in ("http://127.0.0.1:4566", "http://100.100.154.68:4566"):
        raise ValueError("Only laptop loopback or 100.100.154.68:4566 authorized")
    return value.rstrip("/")


def report_dir():
    result = ROOT / "reports/laptop" / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6])
    result.mkdir(parents=True)
    return result


def public_report(value):
    if isinstance(value, dict):
        return {k: public_report(v) for k, v in value.items() if k != "downloadUrl"}
    if isinstance(value, list): return [public_report(v) for v in value]
    return value


def template(name):
    value = yaml.safe_load((HERE / "templates" / (name + ".yaml")).read_text())
    if name == "business":
        value["Resources"]["Workflow"]["Properties"]["DefinitionString"] = {
            "Fn::Sub": (HERE / "workflow.asl.json").read_text()}
    return value


def validate():
    STATE.mkdir(parents=True, exist_ok=True)
    for name in ("artifacts", "business", "runtime-probe"):
        path = STATE / (name + ".json")
        save(path, template(name))
        subprocess.run([str(Path(sys.executable).parent / "cfn-lint"), str(path)], check=True)
    definition = json.loads((HERE / "workflow.asl.json").read_text())
    assert definition["TimeoutSeconds"] < 300
    print("PASS: CloudFormation lint and workflow lease bound")


def package():
    dependencies = STATE / "dependencies"
    if dependencies.is_symlink(): raise ValueError("Dependency directory must not be a symlink")
    if dependencies.exists(): shutil.rmtree(dependencies)
    dependencies.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--only-binary=:all:", "--upgrade",
                    "--target", str(dependencies), "-r", str(HERE / "requirements.txt")], check=True)
    target = STATE / "functions.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for base in (dependencies, HERE / "lambda"):
            for path in sorted(base.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                info = zipfile.ZipInfo(path.relative_to(base).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    return target


def deploy_stack(lab, suffix, body, parameters, update=False):
    name = PREFIX + suffix
    cf = lab.client("cloudformation")
    cf.validate_template(TemplateBody=json.dumps(body))
    existing = lab.stack(name)
    kwargs = dict(StackName=name, TemplateBody=json.dumps(body), Capabilities=["CAPABILITY_IAM"], Tags=[TAG],
                  Parameters=[{"ParameterKey": k, "ParameterValue": str(v)} for k, v in parameters.items()])
    receipt = STATE / (suffix + "-deployed.json")
    if existing:
        if existing["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
            raise ValueError(f"Inspect {name}: {existing['StackStatus']}")
        live = cf.get_template(StackName=name)["TemplateBody"]
        if isinstance(live, str): live = yaml.safe_load(live)
        old_parameters = {p["ParameterKey"]: p["ParameterValue"] for p in existing.get("Parameters", [])}
        if live == body and old_parameters == {k: str(v) for k, v in parameters.items()}:
            print(name + ": unchanged", flush=True)
            return
        if not update:
            raise ValueError("Existing business stack differs. Review generated templates; --update permits safe changes only.")
        if not receipt.exists() or json.loads(receipt.read_text())["template"] != live:
            raise ValueError("Live template differs from deployment receipt or receipt missing; preserve user changes and review manually")
        changed_keys = set(live["Resources"]) ^ set(body["Resources"])
        for key in changed_keys:
            resource = live["Resources"].get(key) or body["Resources"][key]
            if resource["Type"] != "AWS::ApiGateway::Deployment":
                raise ValueError("Resource additions/removals require manual change-set review: " + key)
        for key, old in live["Resources"].items():
            if key in changed_keys: continue
            if old != body["Resources"][key] and old["Type"] not in {
                "AWS::Lambda::Function", "AWS::IAM::Role", "AWS::StepFunctions::StateMachine",
                "AWS::ApiGateway::Method", "AWS::ApiGateway::Deployment", "AWS::ApiGateway::Stage", "AWS::Lambda::Permission"}:
                raise ValueError("Refusing automatic change to " + key)
        cf.update_stack(**kwargs)
    else:
        cf.create_stack(**kwargs)
    wait_stack(lab, name)
    save(receipt, {"endpoint": lab.endpoint, "template": body, "parameters": parameters})


def outputs(lab):
    return lab.outputs("business")


def invoke(lab, function, payload, expect_error=False):
    response = lab.client("lambda").invoke(FunctionName=PREFIX + function, Payload=json.dumps(payload).encode())
    result = json.loads(response["Payload"].read())
    if bool(response.get("FunctionError")) != expect_error:
        raise AssertionError({"function": function, "error": response.get("FunctionError"), "result": result})
    return result


def pipe(lab, desired=None):
    out = outputs(lab)
    pipes = lab.client("pipes")
    try:
        current = pipes.describe_pipe(Name=PIPE)
    except pipes.exceptions.NotFoundException:
        if desired is None: return None
        pipes.create_pipe(Name=PIPE, RoleArn=out["PipeRoleArn"], Source=out["QueueArn"], Target=out["StateMachineArn"],
                          DesiredState="STOPPED", Tags={"Lab": "micro-test-local"},
                          SourceParameters={"SqsQueueParameters": {"BatchSize": 1}},
                          TargetParameters={"StepFunctionStateMachineParameters": {"InvocationType": "FIRE_AND_FORGET"}})
        current = pipes.describe_pipe(Name=PIPE)
    if (current["Source"] != out["QueueArn"] or current["Target"] != out["StateMachineArn"]
            or current["RoleArn"] != out["PipeRoleArn"]
            or pipes.list_tags_for_resource(resourceArn=current["Arn"]).get("tags", {}).get("Lab") != "micro-test-local"):
        raise ValueError("Unowned or modified business Pipe")
    if desired:
        # Installed provider needs UpdatePipe, including after CreatePipe(STOPPED).
        pipes.update_pipe(Name=PIPE, DesiredState=desired)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            current = pipes.describe_pipe(Name=PIPE)
            if current["CurrentState"] == desired: return current
            time.sleep(2)
        raise TimeoutError("Pipe did not reach " + desired)
    return current


def deploy(lab, args):
    if lab.stack(PREFIX + "micro-api"):
        raise ValueError("Legacy micro-api exposes internal routes without auth; retire that optional API before deploying business")
    if not args.runtime:
        raise ValueError("--runtime localstack/.state/business-runtime.json from WSL bootstrap required")
    runtime = json.loads(Path(args.runtime).read_text())
    endpoint(runtime["endpoint"])
    sys.path.insert(0, str(HERE / "lambda"))
    from runtime import local_url
    local_url(runtime["MicroBaseUrl"], 31080)
    local_url(runtime["RuntimeEndpoint"], 4566)
    import ipaddress
    address = ipaddress.ip_address(runtime["DatabaseHost"])
    if not any(address in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise ValueError("Database must use observed Docker node/LocalStack private IP")
    if not 1 <= int(runtime["DatabasePort"]) <= 65535: raise ValueError("Invalid PostgreSQL port")
    validate()
    archive = package()
    deploy_stack(lab, "business-artifacts", template("artifacts"), {}, args.update)
    key = "lambda/" + hashlib.sha256(archive.read_bytes()).hexdigest() + ".zip"
    lab.client("s3").put_object(Bucket=lab.outputs("business-artifacts")["BucketName"], Key=key, Body=archive.read_bytes())
    parameters = {k: runtime[k] for k in ("MicroBaseUrl", "RuntimeEndpoint", "DatabaseHost", "DatabasePort")}
    body = template("business")
    # API deployments are snapshots. A new code/runtime target must redeploy its stage.
    deployment = "Deployment" + hashlib.sha256((key + runtime["MicroBaseUrl"] + json.dumps(body, sort_keys=True)).encode()).hexdigest()[:12]
    body["Resources"][deployment] = body["Resources"].pop("Deployment")
    body["Resources"]["Stage"]["Properties"]["DeploymentId"] = {"Ref": deployment}
    deploy_stack(lab, "business", body, parameters | {"CodeKey": key}, args.update)
    pipe(lab, "STOPPED")
    for function in ("publisher", "transport", "authorizer"):
        lab.client("lambda").get_waiter("function_active_v2").wait(FunctionName=PREFIX + function)
        lab.client("lambda").get_waiter("function_updated_v2").wait(FunctionName=PREFIX + function)
    if not invoke(lab, "publisher", {"health": True})["schemaReady"]:
        raise ValueError("Publisher reaches PostgreSQL but application outbox schema is missing; complete WSL micro bootstrap")
    invoke(lab, "transport", {"operation": "health"})
    print("Runtime reachable. Run 'start', then 'http' and 'test'.")


def probe(lab, update=False):
    archive = package()
    deploy_stack(lab, "business-artifacts", template("artifacts"), {}, update)
    key = "lambda/" + hashlib.sha256(archive.read_bytes()).hexdigest() + ".zip"
    lab.client("s3").put_object(Bucket=lab.outputs("business-artifacts")["BucketName"], Key=key, Body=archive.read_bytes())
    deploy_stack(lab, "business-probe", template("runtime-probe"),
                 {"CodeKey": key, "DatabasePort": lab.outputs("database")["Port"]}, update)
    lab.client("lambda").get_waiter("function_active_v2").wait(FunctionName=PREFIX + "business-probe")
    lab.client("lambda").get_waiter("function_updated_v2").wait(FunctionName=PREFIX + "business-probe")
    return invoke(lab, "business-probe", {"health": True})


def token(lab, kind="valid"):
    key = json.loads(lab.client("secretsmanager").get_secret_value(SecretId=outputs(lab)["AuthSecretArn"])["SecretString"])["key"]
    now = int(time.time())
    exp = now + 3600 if kind != "expired" else now - 1
    iat = now if kind != "expired" else now - 3600
    def encode(value): return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")
    message = encode({"alg": "HS256", "typ": "JWT"}) + "." + encode({"iss": "micro-test-local", "aud": "micro-test-local-purchases", "sub": "lab-student", "iat": iat, "exp": exp})
    signature = hmac.new((key if kind != "invalid" else "wrong-local-key").encode(), message.encode(), hashlib.sha256).digest()
    return message + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")


def base_url(lab):
    return lab.endpoint + "/_aws/execute-api/" + outputs(lab)["ApiId"] + "/lab"


def http_request(lab, path, method="GET", body=None, bearer=None, failure=None):
    headers = {"Content-Type": "application/json"}
    if bearer: headers["Authorization"] = "Bearer " + bearer
    if failure: headers["X-Lab-Failure"] = failure
    request = urllib.request.Request(base_url(lab) + path, None if body is None else json.dumps(body).encode(), headers, method)
    try:
        with OPENER.open(request, timeout=25) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try: value = json.loads(raw)
        except ValueError: value = {"body": raw}
        return exc.code, value


def http_file(lab):
    path = STATE / "purchase.http"
    contents = (HERE / "requests/purchase.http").read_text()
    for key, value in {"__BASE_URL__": base_url(lab), "__TOKEN__": token(lab), "__EXPIRED__": token(lab, "expired"),
                       "__INVALID__": token(lab, "invalid"), "__REFERENCE__": "practice-" + uuid.uuid4().hex}.items():
        contents = contents.replace(key, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(0o600)
    print(str(path) + " (tokens expire in one hour; ignored by Git)")
    return {"file": str(path), "tokenLifetimeSeconds": 3600}


def purchase(lab, bearer, failure=None):
    body = {"externalReference": "proof-" + uuid.uuid4().hex, "customerName": "Local practice",
            "customerEmail": "practice@example.com", "productCode": "AUTO-01"}
    code, value = http_request(lab, "/purchases", "POST", body, bearer, failure)
    assert code == 202, (code, value)
    return body, value


def wait_sale(lab, bearer, sale_id, wanted="COMPLETED", timeout=180):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        code, value = http_request(lab, "/purchases/" + sale_id, bearer=bearer)
        assert code == 200, (code, value)
        if value["status"] == wanted: return value
        if value["status"] == "FAILED" and wanted != "FAILED": raise AssertionError(value)
        if value["status"] != last:
            print(sale_id + ": " + value["status"], flush=True)
            last = value["status"]
        time.sleep(2)
    raise TimeoutError(f"Sale {sale_id} did not reach {wanted}")


def pdf_proof(lab, sale):
    obj = lab.client("s3").get_object(Bucket=lab.outputs("storage")["BucketName"], Key=sale["document"]["object_key"])
    data = obj["Body"].read()
    assert obj["ContentType"] == "application/pdf" and data.startswith(b"%PDF-")
    url = urllib.parse.urlsplit(sale["downloadUrl"])
    endpoint(url.scheme + "://" + url.netloc)
    with OPENER.open(sale["downloadUrl"], timeout=15) as response:
        assert response.read() == data
    return {"key": sale["document"]["object_key"], "sha256": hashlib.sha256(data).hexdigest(), "versionId": obj.get("VersionId")}


def executions(lab, event_id):
    states = lab.client("stepfunctions")
    matches = []
    for page in states.get_paginator("list_executions").paginate(stateMachineArn=outputs(lab)["StateMachineArn"]):
        for item in page["executions"]:
            execution = states.describe_execution(executionArn=item["executionArn"])
            if event_id in execution.get("input", ""):
                matches.append({k: execution.get(k) for k in ("executionArn", "status", "error", "cause")})
    return matches


def wait_executions(lab, event_id, expected="SUCCEEDED", minimum=1):
    deadline = time.monotonic() + 60
    runs = []
    while time.monotonic() < deadline:
        runs = executions(lab, event_id)
        if len(runs) >= minimum and all(x["status"] == expected for x in runs): return runs
        time.sleep(2)
    raise AssertionError({"eventId": event_id, "expected": expected, "executions": runs})


def smoke(lab, proof):
    bearer = token(lab)
    assert pipe(lab)["CurrentState"] == "RUNNING", "Run start first"
    assert lab.client("events").describe_rule(Name=PREFIX + "publisher")["State"] == "ENABLED"
    for kind in ("missing", "invalid", "expired"):
        code, value = http_request(lab, "/purchases/" + str(uuid.uuid4()), bearer=None if kind == "missing" else token(lab, kind))
        assert code in (401, 403), (kind, code, value)
        proof["auth_" + kind] = {"status": "PASS", "http": code}
    body, accepted = purchase(lab, bearer)
    code, duplicate = http_request(lab, "/purchases", "POST", body, bearer)
    assert code == 202 and duplicate["saleId"] == accepted["saleId"] and duplicate["eventId"] == accepted["eventId"]
    assert len(duplicate["outbox"]) == 1
    code, conflict = http_request(lab, "/purchases", "POST", body | {"productCode": "DIFFERENT"}, bearer)
    assert code == 409, (code, conflict)
    completed = wait_sale(lab, bearer, accepted["saleId"])
    assert completed["outbox"][0].get("published_at"), completed
    proof["authenticated_periodic_end_to_end"] = {"status": "PASS", "sale": completed, "pdf": pdf_proof(lab, completed),
                                                  "executions": wait_executions(lab, accepted["eventId"])}
    proof["reference_idempotency"] = {"status": "PASS", "saleId": accepted["saleId"], "eventId": accepted["eventId"]}
    _, transient = purchase(lab, bearer, "document:transient:2")
    recovered = wait_sale(lab, bearer, transient["saleId"])
    transient_runs = wait_executions(lab, transient["eventId"])
    history = lab.client("stepfunctions").get_execution_history(executionArn=transient_runs[0]["executionArn"], maxResults=1000)["events"]
    failures = [e for e in history if e.get("type") in ("TaskFailed", "LambdaFunctionFailed") and "TransientStepError" in json.dumps(e)]
    assert len(failures) >= 2, "Controlled transient failures were not observed in execution history"
    proof["transient"] = {"status": "PASS", "sale": recovered, "executions": transient_runs, "transientFailures": len(failures)}
    _, permanent = purchase(lab, bearer, "upload:permanent:1")
    failed = wait_sale(lab, bearer, permanent["saleId"], "FAILED")
    assert failed.get("error") and failed["recoverable"]
    failed_runs = wait_executions(lab, permanent["eventId"], "FAILED")
    code, retry = http_request(lab, "/purchases/" + failed["saleId"] + "/retry", "POST", {}, bearer)
    assert code == 202 and retry["eventId"] != failed["eventId"]
    recovered = wait_sale(lab, bearer, failed["saleId"])
    proof["permanent_and_recovery"] = {"status": "PASS", "failed": failed, "failedExecutions": failed_runs,
                                        "recovered": recovered, "recoveryExecutions": wait_executions(lab, retry["eventId"])}
    events = lab.client("events")
    events.disable_rule(Name=PREFIX + "publisher")
    try:
        _, crash = purchase(lab, bearer)
        result = invoke(lab, "publisher", {"eventId": crash["eventId"], "crashAfterSendEventId": crash["eventId"]}, expect_error=True)
        assert "LAB_CRASH_AFTER_SEND" in json.dumps(result)
        _, pending = http_request(lab, "/purchases/" + crash["saleId"], bearer=bearer)
        assert pending["outbox"][0].get("published_at") is None
        time.sleep(6)
        invoke(lab, "publisher", {"eventId": crash["eventId"]})
        done = wait_sale(lab, bearer, crash["saleId"])
        runs = wait_executions(lab, crash["eventId"], minimum=2)
        proof["publisher_crash_duplicate"] = {"status": "PASS", "sale": done, "pdf": pdf_proof(lab, done), "executions": runs}
    finally:
        events.enable_rule(Name=PREFIX + "publisher")
    invalid_id = str(uuid.uuid4())
    lab.client("sqs").send_message(QueueUrl=outputs(lab)["QueueUrl"], MessageBody=json.dumps({"version": 99, "eventId": invalid_id}))
    deadline = time.monotonic() + 60
    runs = []
    while time.monotonic() < deadline:
        runs = executions(lab, invalid_id)
        if runs and all(x["status"] == "FAILED" for x in runs): break
        time.sleep(2)
    assert runs and all(x["status"] == "FAILED" for x in runs), runs
    proof["invalid_event"] = {"status": "PASS", "executions": runs}
    return proof


def snapshot(lab, sale_id, target, restore=False):
    if restore:
        original = json.loads(Path(target).read_text())
        if original["endpoint"] != lab.endpoint: raise ValueError("Snapshot belongs to another endpoint")
        sale_id = original["saleId"]
        # Read original S3 version before any application call. Never regenerate data.
        obj = lab.client("s3").get_object(Bucket=original["bucket"], Key=original["pdf"]["key"])
        assert hashlib.sha256(obj["Body"].read()).hexdigest() == original["pdf"]["sha256"]
        assert obj.get("VersionId") == original["pdf"]["versionId"]
    if not sale_id: raise ValueError("--sale-id required")
    code, sale = http_request(lab, "/purchases/" + str(uuid.UUID(sale_id)), bearer=token(lab))
    assert code == 200 and sale["status"] == "COMPLETED", (code, sale)
    result = {"endpoint": lab.endpoint, "saleId": sale_id, "bucket": lab.outputs("storage")["BucketName"],
              "sale": {k: sale.get(k) for k in ("saleId", "eventId", "generation", "status", "outbox", "document", "executionArn")},
              "pdf": pdf_proof(lab, sale)}
    execution = lab.client("stepfunctions").describe_execution(executionArn=sale["executionArn"])
    assert execution["status"] == "SUCCEEDED", execution["status"]
    result["workflow"] = {"executionArn": execution["executionArn"], "status": execution["status"],
                          "startDate": execution["startDate"].isoformat(), "stopDate": execution["stopDate"].isoformat()}
    if restore:
        assert result == original, "Original database/outbox/object state changed"
        return {"status": "PASS", "original": result}
    save(target, result)
    return {"snapshot": str(target), "persistence": "PENDIENTE: verify after controlled restart"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "package", "status", "probe", "deploy", "start", "stop", "publish", "http", "test", "iam", "dlq", "snapshot", "verify-snapshot"])
    parser.add_argument("--endpoint", type=endpoint)
    parser.add_argument("--runtime")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--event-id")
    parser.add_argument("--crash-after-send", action="store_true")
    parser.add_argument("--sale-id")
    parser.add_argument("--snapshot", default=str(STATE / "persistence.json"))
    args = parser.parse_args()
    if args.command == "validate": return validate()
    if args.command == "package": print(package()); return
    if not args.endpoint: parser.error("--endpoint required")
    lab = Lab(args.endpoint)
    report = report_dir()
    result = {"endpoint": lab.endpoint, "localstack": lab.info, "command": args.command, "checks": {}}
    try:
        if args.command == "status":
            result["persistence"] = lab.health.get("features")
            result["stacks"] = {suffix: (lab.stack(PREFIX + suffix) or {}).get("StackStatus", "ABSENT")
                                for suffix in ("network", "eks", "registry", "database", "storage", "queues", "lambda", "workflow", "pipe", "api", "micro-api", "business-artifacts", "business-probe", "business")}
            if lab.stack(PREFIX + "business"):
                result["outputs"] = outputs(lab)
                result["pipe"] = pipe(lab)
        elif args.command == "deploy": deploy(lab, args)
        elif args.command == "probe": result["result"] = probe(lab, args.update)
        elif args.command == "start":
            if not invoke(lab, "publisher", {"health": True})["schemaReady"]:
                raise ValueError("Application outbox schema missing; complete WSL bootstrap")
            invoke(lab, "transport", {"operation": "health"})
            pipe(lab, "RUNNING")
            lab.client("events").enable_rule(Name=PREFIX + "publisher")
        elif args.command == "stop":
            lab.client("events").disable_rule(Name=PREFIX + "publisher")
            pipe(lab, "STOPPED")
        elif args.command == "publish":
            event = {"limit": 20}
            if args.event_id: event["eventId"] = str(uuid.UUID(args.event_id))
            if args.crash_after_send:
                if not args.event_id: raise ValueError("--event-id required for targeted crash test")
                event["crashAfterSendEventId"] = event["eventId"]
            result["result"] = invoke(lab, "publisher", event, args.crash_after_send)
        elif args.command == "http": result["result"] = http_file(lab)
        elif args.command == "test": smoke(lab, result["checks"])
        elif args.command == "iam":
            import checks
            checks.iam(lab, report)
        elif args.command == "dlq":
            import checks
            checks.queues(lab, report)
        else:
            result["result"] = snapshot(lab, args.sale_id, args.snapshot, args.command == "verify-snapshot")
        result["status"] = "PASS"
    except Exception as exc:
        result.update(status="FAIL", error=str(exc))
        raise
    finally:
        result = public_report(result)
        save(report / (args.command + ".json"), result)
        print(json.dumps(result, indent=2, default=str), flush=True)
        print("Report: " + str(report), flush=True)


if __name__ == "__main__":
    main()
