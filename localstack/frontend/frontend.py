#!/usr/bin/env python3
"""Build and publish the Angular UI to the owned LocalStack S3 frontend bucket."""
import argparse
import json
import mimetypes
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LAB_ROOT = HERE.parent
sys.path.insert(0, str(LAB_ROOT / "business"))
sys.path.insert(0, str(LAB_ROOT / "scripts"))
from business import endpoint
from common import Lab, PREFIX, TAG

STACK = PREFIX + "frontend"
BUCKET = PREFIX + "frontend"
BUILD = ROOT / "frontend" / "dist" / "sales-ui" / "browser"
TEMPLATE = HERE / "templates" / "frontend.yaml"


def template():
    import yaml
    return yaml.safe_load(TEMPLATE.read_text())


def verify_api_gateway(lab):
    business = lab.stack(PREFIX + "business")
    if not business:
        raise ValueError("Business API is not deployed; run the lab bootstrap first")
    api_id = lab.outputs("business")["ApiId"]
    api = lab.client("apigateway")
    authorizers = api.get_authorizers(restApiId=api_id)["items"]
    matches = [item for item in authorizers if item.get("name") == "purchase-jwt"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one purchase-jwt API Gateway Lambda authorizer")
    authorizer = matches[0]
    if (authorizer.get("type") != "TOKEN"
            or authorizer.get("identitySource") != "method.request.header.Authorization"
            or ":function:micro-test-local-authorizer/invocations" not in authorizer.get("authorizerUri", "")):
        raise ValueError("API Gateway authorizer does not match the deployed local JWT Lambda")
    resources = api.get_resources(restApiId=api_id, limit=500)["items"]
    for path, verb in (("/purchases", "POST"), ("/purchases/{saleId}", "GET"),
                       ("/purchases/{saleId}/retry", "POST")):
        resource = next((item for item in resources if item.get("path") == path), None)
        if not resource:
            raise ValueError("Missing authenticated API Gateway route: " + verb + " " + path)
        method = api.get_method(restApiId=api_id, resourceId=resource["id"], httpMethod=verb)
        if method.get("authorizationType") != "CUSTOM" or method.get("authorizerId") != authorizer["id"]:
            raise ValueError("Route is not protected by purchase-jwt: " + verb + " " + path)
    return api_id


def deploy_stack(lab):
    body = template()
    cf = lab.client("cloudformation")
    cf.validate_template(TemplateBody=json.dumps(body))
    current = lab.stack(STACK)
    if current:
        if current["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
            raise ValueError("Inspect frontend stack before deploying: " + current["StackStatus"])
        live = cf.get_template(StackName=STACK)["TemplateBody"]
        if isinstance(live, str):
            import yaml
            live = yaml.safe_load(live)
        if live != body:
            raise ValueError("Existing frontend stack differs; preserve it and review before changing")
        return lab.outputs("frontend")
    cf.create_stack(StackName=STACK, TemplateBody=json.dumps(body), Tags=[TAG])
    waiter = cf.get_waiter("stack_create_complete")
    waiter.wait(StackName=STACK, WaiterConfig={"Delay": 2, "MaxAttempts": 90})
    return lab.outputs("frontend")


def build():
    if not (ROOT / "frontend" / "package.json").is_file():
        raise ValueError("Angular frontend/package.json is missing")
    subprocess.run(["npm", "run", "build:localstack"], cwd=ROOT / "frontend", check=True)
    if not BUILD.is_dir() or not (BUILD / "index.html").is_file():
        raise ValueError("Angular build did not produce dist/sales-ui/browser/index.html")
    files = sorted(
        (path for path in BUILD.rglob("*") if path.is_file()),
        key=lambda path: (path.name == "index.html", path.relative_to(BUILD).as_posix()))
    if not files:
        raise ValueError("Angular build output is empty")
    return files


def publish(lab, files):
    s3 = lab.client("s3")
    for path in files:
        if path.is_symlink():
            raise ValueError("Refusing symlink in Angular build output: " + str(path))
        key = path.relative_to(BUILD).as_posix()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        cache_control = "no-cache" if path.name == "index.html" else "public, max-age=31536000, immutable"
        s3.put_object(Bucket=BUCKET, Key=key, Body=path.read_bytes(),
                      ContentType=content_type, CacheControl=cache_control)
    response = s3.get_object(Bucket=BUCKET, Key="index.html")
    try:
        if response["Body"].read() != (BUILD / "index.html").read_bytes():
            raise ValueError("Uploaded S3 index.html did not verify")
    finally:
        response["Body"].close()


def run(action, endpoint_url):
    lab = Lab(endpoint_url)
    if lab.endpoint != "http://127.0.0.1:4566":
        raise ValueError("Frontend deployment requires the lab's local loopback endpoint")
    api_id = verify_api_gateway(lab)
    if action == "deploy":
        files = build()
        outputs = deploy_stack(lab)
        publish(lab, files)
    else:
        outputs = lab.outputs("frontend")
        if lab.stack(STACK)["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
            raise ValueError("Frontend S3 stack is not ready")
        s3 = lab.client("s3")
        s3.head_object(Bucket=outputs["BucketName"], Key="index.html")
        files = []
    print(json.dumps({
        "status": "PASS",
        "bucket": outputs["BucketName"],
        "frontendUrl": outputs["FrontendUrl"],
        "apiId": api_id,
        "authorizer": "micro-test-local-authorizer",
        "publishedFiles": len(files) if action == "deploy" else "unchanged",
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("deploy", "status"))
    parser.add_argument("--endpoint", required=True, type=endpoint)
    args = parser.parse_args()
    run(args.command, args.endpoint)


if __name__ == "__main__":
    main()
