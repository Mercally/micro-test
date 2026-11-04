#!/usr/bin/env python3
"""Deploy and test independent LocalStack stacks; never selects an AWS endpoint."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

import yaml
from botocore.exceptions import ClientError
from common import ROOT, PREFIX, TAG, Lab, endpoint_url, http, save

FILES = sorted((ROOT / "cloudformation").glob("*.yaml"))
DATA = {"network", "eks", "registry", "database", "storage", "queues"}


def template(path):
    data = yaml.safe_load(path.read_text())
    resources = data["Resources"]
    if path.name == "07-lambda.yaml":
        resources["Function"]["Properties"]["Code"]["ZipFile"] = (ROOT / "fixtures/diagnostic.py").read_text()
    if path.name == "08-workflow.yaml":
        resources["Machine"]["Properties"]["DefinitionString"] = (ROOT / "fixtures/diagnostic.asl.json").read_text()
    return data


def suffix(path):
    return path.stem.split("-", 1)[1]


def validate():
    exports, imports = {}, []
    paths = []
    for path in FILES:
        data = template(path)
        rendered = ROOT / ".state/rendered" / path.name
        rendered.parent.mkdir(parents=True, exist_ok=True)
        rendered.write_text(yaml.safe_dump(data, sort_keys=False))
        paths.append(str(rendered))
        for output in data.get("Outputs", {}).values():
            if "Export" in output:
                name = output["Export"]["Name"]
                if not name.startswith(PREFIX) or name in exports:
                    raise ValueError(f"Unsafe/duplicate export: {name}")
                exports[name] = path.name
        def visit(value):
            if isinstance(value, dict):
                if "Fn::ImportValue" in value:
                    imports.append((path.name, value["Fn::ImportValue"]))
                for v in value.values(): visit(v)
            if isinstance(value, list):
                for v in value: visit(v)
        visit(data)
    for path, name in imports:
        if name not in exports or exports[name] >= path:
            raise ValueError(f"Missing or out-of-order import: {path} -> {name}")
    subprocess.run([str(Path(sys.executable).with_name("cfn-lint")), "--non-zero-exit-code", "error", "--template", *paths], check=True)
    print(f"PASS: {len(paths)} templates; schemas, references, exports and dependency order", flush=True)


def wait_stack(lab, name, deleting=False):
    deadline = time.monotonic() + 900
    previous = None
    while time.monotonic() < deadline:
        stack = lab.stack(name)
        if not stack:
            if deleting: return
            raise RuntimeError(f"Stack disappeared: {name}")
        status = stack["StackStatus"]
        if status != previous:
            print(f"{name}: {status}", flush=True)
            previous = status
        if not deleting and status in ("CREATE_COMPLETE", "UPDATE_COMPLETE"): return
        if "FAILED" in status or "ROLLBACK" in status:
            events = lab.client("cloudformation").describe_stack_events(StackName=name)["StackEvents"]
            raise RuntimeError(json.dumps([{k:e.get(k) for k in ("LogicalResourceId", "ResourceStatus", "ResourceStatusReason")} for e in events[:8]], indent=2))
        time.sleep(3)
    raise TimeoutError(f"Stack wait expired: {name}; inspect status before retrying")


def deploy(lab, selected, parameters=None):
    cf = lab.client("cloudformation")
    for path in FILES:
        if selected and suffix(path) not in selected: continue
        if not selected and suffix(path) == "micro-api": continue
        name = PREFIX + suffix(path)
        body = json.dumps(template(path))
        cf.validate_template(TemplateBody=body)
        existing = lab.stack(name)
        kwargs = dict(StackName=name, TemplateBody=body, Capabilities=["CAPABILITY_NAMED_IAM"], Tags=[TAG])
        if suffix(path) == "micro-api":
            import ipaddress
            if not parameters: raise ValueError("micro-api requires --parameters from WSL bootstrap")
            values = json.loads(Path(parameters).read_text())
            url = urlsplit(values["MicroBaseUrl"])
            address = ipaddress.ip_address(url.hostname)
            if not (url.scheme == "http" and address.version == 4 and address.is_private
                    and not address.is_loopback and url.port == 31080
                    and not url.username and not url.password and not url.query and not url.fragment
                    and url.path == ""):
                raise ValueError("MicroBaseUrl must be the observed Docker node IP on port 31080")
            kwargs["Parameters"] = [{"ParameterKey":"MicroBaseUrl", "ParameterValue":values["MicroBaseUrl"]}]
        if existing:
            if existing["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                raise ValueError(f"Inspect existing stack before retry: {name} {existing['StackStatus']}")
            # Avoid automatic data replacement or updates to live resources.
            old = cf.get_template(StackName=name)["TemplateBody"]
            if isinstance(old, str): old = yaml.safe_load(old)
            if old != template(path):
                raise ValueError(f"Template changed for {name}; review a change set manually. No automatic replacement.")
            print(f"{name}: unchanged", flush=True)
            if suffix(path) == "pipe": ensure_pipe(lab)
            continue
        cf.create_stack(**kwargs)
        wait_stack(lab, name)
        if suffix(path) == "pipe": ensure_pipe(lab)


def owned_pipe(lab):
    try:
        pipe = lab.client("pipes").describe_pipe(Name=PREFIX + "diagnostic")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NotFoundException": return None
        raise
    if pipe.get("Tags", {}).get("Lab") != "micro-test-local":
        raise ValueError("Refusing unowned Pipe")
    return pipe


def ensure_pipe(lab):
    pipes = lab.client("pipes")
    expected = dict(RoleArn=lab.outputs("pipe")["RoleArn"], Source=lab.outputs("queues")["QueueArn"],
                    Target=lab.outputs("workflow")["StateMachineArn"])
    current = owned_pipe(lab)
    if current:
        if any(current[k] != v for k,v in expected.items()): raise ValueError("Existing Pipe has different dependencies")
        if current["CurrentState"] != "STOPPED": raise ValueError("Existing Pipe is not STOPPED; inspect before deployment")
        return
    fixture = json.loads((ROOT / "fixtures/pipe.json").read_text())
    pipes.create_pipe(**fixture, **expected)
    try:
        # This release can leave a correctly requested STOPPED Pipe in CREATING.
        # Reassert STOPPED with UpdatePipe; never activate it during bootstrap.
        pipes.update_pipe(Name=fixture["Name"], RoleArn=expected["RoleArn"], DesiredState="STOPPED")
        wait_pipe(pipes, "STOPPED")
    except Exception:
        pipes.delete_pipe(Name=fixture["Name"])
        raise
    print("PASS: API-managed diagnostic Pipe created STOPPED (CloudFormation property gap)", flush=True)


def set_pipe_state(lab, desired):
    pipe = owned_pipe(lab)
    if not pipe: raise ValueError("Missing diagnostic Pipe")
    # Installed 2026.10.4 rejects StartPipe/StopPipe. UpdatePipe implements this transition.
    lab.client("pipes").update_pipe(Name=pipe["Name"], RoleArn=pipe["RoleArn"], DesiredState=desired)
    wait_pipe(lab.client("pipes"), desired)


def probe(lab):
    report = {"checkedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "endpoint": lab.endpoint, "localstack": lab.info,
              "persistence": lab.health.get("features", {}).get("persistence"), "resources": {}, "apis": {}}
    cf = lab.client("cloudformation")
    kinds = sorted({r["Type"] for p in FILES for r in template(p)["Resources"].values()})
    for kind in kinds:
        try:
            result = cf.describe_type(Type="RESOURCE", TypeName=kind)
            report["resources"][kind] = {"schemaAvailable": bool(result.get("Schema")),
                                        "note": "Registry schema is not proof of implemented CRUD/properties"}
        except ClientError as exc:
            report["resources"][kind] = {"error": exc.response["Error"]}
    reads = {"eks": ("list_clusters", {}), "ecr": ("describe_repositories", {}),
             "rds": ("describe_db_instances", {}), "pipes": ("list_pipes", {"NamePrefix": PREFIX}),
             "lambda": ("list_functions", {}), "stepfunctions": ("list_state_machines", {}),
             "apigateway": ("get_rest_apis", {})}
    for service, (operation, kwargs) in reads.items():
        try:
            getattr(lab.client(service), operation)(**kwargs)
            report["apis"][service + "." + operation] = "responded; CRUD/runtime still require deployment tests"
        except ClientError as exc: report["apis"][service + "." + operation] = exc.response["Error"]
    save(ROOT / "reports/coverage.json", report)
    print(json.dumps(report, indent=2))


def status(lab):
    states = {}
    for path in FILES:
        name = PREFIX + suffix(path)
        stack = lab.stack(name)
        states[name] = stack["StackStatus"] if stack else "ABSENT"
    print(json.dumps({"localstack": lab.info, "persistence": lab.health.get("features"), "stacks": states}, indent=2))


def invoke(lab, payload):
    response = lab.client("lambda").invoke(FunctionName=PREFIX + "diagnostic", Payload=json.dumps(payload).encode())
    value = json.loads(response["Payload"].read())
    if response.get("FunctionError") or value.get("messages") != [payload]:
        raise AssertionError(value)
    return value


def smoke(lab):
    payload = {"diagnosticId": "local-" + str(uuid.uuid4()), "kind": "integration-only", "value": 73}
    result = {"endpoint": lab.endpoint, "localstack": lab.info, "payload": payload, "lambda": invoke(lab, payload)}
    api = lab.outputs("api")["ApiId"]
    # Path routing uses the explicit gateway. Do not follow service-returned public URLs.
    url = lab.endpoint + f"/_aws/execute-api/{api}/lab/diagnostic"
    value = http(url, payload)
    if value != result["lambda"]: raise AssertionError(value)
    result["apiGateway"] = value
    pipes, states = lab.client("pipes"), lab.client("stepfunctions")
    pipe = pipes.describe_pipe(Name=PREFIX + "diagnostic")
    machine = lab.outputs("workflow")["StateMachineArn"]
    queue = lab.outputs("queues")["QueueUrl"]
    if pipe["Target"] != machine or pipe["Source"] != lab.outputs("queues")["QueueArn"]:
        raise AssertionError("Pipe source/target mismatch")
    policy = lab.client("iam").get_role_policy(RoleName=pipe["RoleArn"].split("/")[-1], PolicyName=PREFIX + "access")["PolicyDocument"]
    required = {"states:StartExecution": machine,
                **{a: pipe["Source"] for a in ("sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes")}}
    for action, resource in required.items():
        if not any(s["Effect"] == "Allow" and action in s["Action"] and s["Resource"] == resource for s in policy["Statement"]):
            raise AssertionError(f"Pipe role lacks {action} on expected resource")
    # Verify the actual target before starting the previously stopped Pipe.
    execution = states.start_execution(stateMachineArn=machine, input=json.dumps(payload))["executionArn"]
    wait_execution(states, execution, payload)
    if pipe["CurrentState"] != "STOPPED": raise ValueError("Diagnostic Pipe must start STOPPED")
    complete = False
    try:
        set_pipe_state(lab, "RUNNING")
        lab.client("sqs").send_message(QueueUrl=queue, MessageBody=json.dumps(payload))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            for e in states.list_executions(stateMachineArn=machine, maxResults=100)["executions"]:
                if e["executionArn"] == execution: continue
                d = states.describe_execution(executionArn=e["executionArn"])
                if payload["diagnosticId"] in d.get("input", ""):
                    result["pipeExecution"] = wait_execution(states, e["executionArn"], payload)
                    for record in result["pipeExecution"]["input"]:
                        record.pop("receiptHandle", None)
                    complete = True
                    break
            if complete: break
            time.sleep(2)
        if not complete: raise TimeoutError("No Pipe-created execution with the expected diagnosticId")
    finally:
        set_pipe_state(lab, "STOPPED")
    result["pipeFinalState"] = "STOPPED"
    result["lifecycleTransport"] = "CreatePipe(STOPPED) + UpdatePipe(STOPPED), UpdatePipe(RUNNING/STOPPED); local compatibility workaround"
    save(ROOT / "reports/diagnostic.json", result)
    print(json.dumps(result, indent=2, default=str))


def wait_execution(states, arn, payload):
    for unused in range(45):
        value = states.describe_execution(executionArn=arn)
        if value["status"] == "SUCCEEDED":
            output = json.loads(value["output"])
            if output.get("messages") != [payload] or output.get("diagnostic") is not True:
                raise AssertionError(output)
            return {"executionArn": arn, "input": json.loads(value["input"]), "output": output, "status": value["status"]}
        if value["status"] != "RUNNING": raise AssertionError(value)
        time.sleep(2)
    raise TimeoutError(arn)


def wait_pipe(pipes, desired):
    for unused in range(45):
        value = pipes.describe_pipe(Name=PREFIX + "diagnostic")
        if value["CurrentState"] == desired: return
        if "FAILED" in value["CurrentState"]: raise RuntimeError(value)
        time.sleep(2)
    raise TimeoutError(f"Pipe did not reach {desired}")


def cleanup(lab, delete_data):
    # Keep data-bearing stacks and their VPC dependencies by default.
    keep = DATA | {"pipe", "workflow", "lambda"}
    if owned_pipe(lab):
        set_pipe_state(lab, "STOPPED")
        if delete_data:
            # 2026.10.4 can stall DeletePipe while STOPPED. This workaround can consume
            # queued diagnostics, so it is reserved for explicit destructive cleanup.
            set_pipe_state(lab, "RUNNING")
            lab.client("pipes").delete_pipe(Name=PREFIX + "diagnostic")
            for unused in range(45):
                if not owned_pipe(lab): break
                time.sleep(2)
            else: raise TimeoutError("Pipe deletion not complete; keeping dependencies")
    for path in reversed(FILES):
        name, kind = PREFIX + suffix(path), suffix(path)
        if kind in keep and not delete_data:
            print(f"KEEP data/dependency stack: {name}")
            continue
        if not lab.stack(name): continue
        if delete_data and kind == "storage":
            s3 = lab.client("s3")
            bucket = lab.outputs("storage")["BucketName"]
            for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
                objects = [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in page.get("Versions", []) + page.get("DeleteMarkers", [])]
                if objects: s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
        if delete_data and kind == "registry":
            ecr = lab.client("ecr")
            for page in ecr.get_paginator("list_images").paginate(repositoryName=PREFIX + "docs"):
                if page["imageIds"]: ecr.batch_delete_image(repositoryName=PREFIX + "docs", imageIds=page["imageIds"])
        lab.client("cloudformation").delete_stack(StackName=name)
        wait_stack(lab, name, deleting=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, type=endpoint_url)
    parser.add_argument("command", choices=["validate", "probe", "deploy", "status", "smoke", "cleanup"])
    parser.add_argument("--only", nargs="+", choices=[suffix(p) for p in FILES])
    parser.add_argument("--delete-data", action="store_true", help="Irreversibly delete this lab's data stacks and objects")
    parser.add_argument("--parameters", help="localstack/.state/micro-api-parameters.json from WSL bootstrap")
    args = parser.parse_args()
    if args.only and args.command != "deploy": parser.error("--only is supported only by deploy")
    if args.parameters and args.command != "deploy": parser.error("--parameters is supported only by deploy")
    if args.delete_data and args.command != "cleanup": parser.error("--delete-data is supported only by cleanup")
    lab = Lab(args.endpoint)
    if args.command == "validate": validate()
    elif args.command == "probe": probe(lab)
    elif args.command == "deploy":
        validate()
        deploy(lab, args.only, args.parameters)
    elif args.command == "status": status(lab)
    elif args.command == "smoke": smoke(lab)
    elif args.command == "cleanup": cleanup(lab, args.delete_data)


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
