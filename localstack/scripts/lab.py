#!/usr/bin/env python3
"""Deploy and test independent LocalStack stacks; never selects an AWS endpoint."""
import json
from pathlib import Path
import subprocess
import sys
import time

import yaml
from common import ROOT, PREFIX, TAG

FILES = sorted((ROOT / "cloudformation").glob("*.yaml"))


def template(path):
    return yaml.safe_load(path.read_text())


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


def deploy(lab, selected=None):
    cf = lab.client("cloudformation")
    for path in FILES:
        if selected and suffix(path) not in selected:
            continue
        name = PREFIX + suffix(path)
        body = template(path)
        cf.validate_template(TemplateBody=json.dumps(body))
        existing = lab.stack(name)
        if existing:
            if existing["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                raise ValueError(f"Inspect existing stack before retry: {name} {existing['StackStatus']}")
            old = cf.get_template(StackName=name)["TemplateBody"]
            if isinstance(old, str): old = yaml.safe_load(old)
            if old != body:
                raise ValueError(f"Template changed for {name}; refusing automatic data replacement")
            print(name + ": unchanged", flush=True)
            continue
        cf.create_stack(StackName=name, TemplateBody=json.dumps(body),
                        Capabilities=["CAPABILITY_NAMED_IAM"], Tags=[TAG])
        wait_stack(lab, name)
