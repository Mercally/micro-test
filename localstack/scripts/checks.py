#!/usr/bin/env python3
"""Runtime checks. A failed or unreachable check never counts as passed."""
import argparse
import hashlib
import json
import sys
import time
import urllib.error
import uuid
from urllib.parse import urlsplit
from botocore.exceptions import ClientError
from common import ROOT, PREFIX, Lab, http, save, endpoint_url, LOCAL_HOSTS


def denied(call):
    try: call()
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("AccessDenied", "AccessDeniedException", "403"):
            return
        raise
    raise AssertionError("Request unexpectedly succeeded; IAM enforcement is absent or policy is ineffective")


def iam(lab, report_dir=None):
    admin, s3 = lab.client("iam"), lab.client("s3")
    bucket = lab.outputs("storage")["BucketName"]
    name = PREFIX + "iam-probe-" + uuid.uuid4().hex[:8]
    key = "_iam/" + name
    user_created = False
    credentials = None
    version = None
    try:
        admin.create_user(UserName=name, Tags=[{"Key":"Lab", "Value":"micro-test-local"}])
        user_created = True
        credentials = lab.create_local_key(name)
        restricted = lab.client("s3", (credentials["AccessKeyId"], credentials["SecretAccessKey"]))
        denied(lambda: restricted.head_bucket(Bucket=bucket))
        version = s3.put_object(Bucket=bucket, Key=key, Body=b"local-iam-proof").get("VersionId")
        policy = {"Version":"2012-10-17", "Statement":[
            {"Effect":"Allow", "Action":"s3:ListBucket", "Resource":f"arn:aws:s3:::{bucket}"},
            {"Effect":"Allow", "Action":"s3:GetObject", "Resource":f"arn:aws:s3:::{bucket}/{key}"}]}
        admin.put_user_policy(UserName=name, PolicyName=PREFIX + "probe", PolicyDocument=json.dumps(policy))
        restricted.head_bucket(Bucket=bucket)
        if restricted.get_object(Bucket=bucket, Key=key)["Body"].read() != b"local-iam-proof":
            raise AssertionError("IAM positive payload mismatch")
        denied(lambda: restricted.put_object(Bucket=bucket, Key=key + "-forbidden", Body=b"denied"))
        policy["Statement"].append({"Effect":"Deny", "Action":"s3:GetObject", "Resource":f"arn:aws:s3:::{bucket}/{key}"})
        admin.put_user_policy(UserName=name, PolicyName=PREFIX + "probe", PolicyDocument=json.dumps(policy))
        denied(lambda: restricted.get_object(Bucket=bucket, Key=key))
        save((report_dir or ROOT / "reports") / "iam.json", {"status":"PASS", "identity":"temporary user with credentials minted by verified local IAM", "checks":["default deny", "scoped allow", "unauthorized write deny", "explicit deny"]})
        print("PASS: restricted identity, default deny, scoped allow, unauthorized write and explicit deny")
    finally:
        if version: s3.delete_object(Bucket=bucket, Key=key, VersionId=version)
        if credentials: admin.delete_access_key(UserName=name, AccessKeyId=credentials["AccessKeyId"])
        if user_created:
            for policy in admin.list_user_policies(UserName=name)["PolicyNames"]:
                admin.delete_user_policy(UserName=name, PolicyName=policy)
            admin.delete_user(UserName=name)


def queues(lab, report_dir=None):
    queues = lab.outputs("queues")
    if lab.stack(PREFIX + "pipe") and lab.client("pipes").describe_pipe(Name=PREFIX + "diagnostic")["CurrentState"] != "STOPPED":
        raise ValueError("Stop diagnostic Pipe before redrive check")
    sqs = lab.client("sqs")
    for url in (queues["QueueUrl"], queues["DlqUrl"]):
        counts = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible", "ApproximateNumberOfMessagesDelayed"])["Attributes"]
        if any(int(c) for c in counts.values()): raise ValueError("Diagnostic queues must be empty before isolated redrive test")
    payload = {"diagnosticId": "redrive-" + str(uuid.uuid4())}
    sqs.send_message(QueueUrl=queues["QueueUrl"], MessageBody=json.dumps(payload))
    receives = 0
    for unused in range(12):
        response = sqs.receive_message(QueueUrl=queues["QueueUrl"], MaxNumberOfMessages=1,
                                       WaitTimeSeconds=2, VisibilityTimeout=1,
                                       MessageSystemAttributeNames=["ApproximateReceiveCount"])
        for m in response.get("Messages", []):
            if json.loads(m["Body"]) != payload:
                sqs.change_message_visibility(QueueUrl=queues["QueueUrl"], ReceiptHandle=m["ReceiptHandle"], VisibilityTimeout=0)
                raise AssertionError("Unexpected message; concurrent producer exists")
            receives += 1
        response = sqs.receive_message(QueueUrl=queues["DlqUrl"], WaitTimeSeconds=2, MaxNumberOfMessages=1)
        if response.get("Messages"):
            m = response["Messages"][0]
            if json.loads(m["Body"]) != payload: raise AssertionError(m)
            sqs.delete_message(QueueUrl=queues["DlqUrl"], ReceiptHandle=m["ReceiptHandle"])
            save((report_dir or ROOT / "reports") / "redrive.json", {"status":"PASS", "payload":payload, "sourceReceives":receives})
            print("PASS: visibility retry and DLQ redrive with matching payload")
            return
        time.sleep(1)
    raise TimeoutError("Expected message did not reach DLQ")


def micro(lab, base, restore=False):
    if not base: raise ValueError("--micro-url required (loopback port-forward or local API Gateway)")
    parsed = urlsplit(base)
    if (parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Micro URL must use loopback or authorized Tailscale host")
    base = base.rstrip("/")
    record = ROOT / ".state/http-proof.json"
    old = json.loads(record.read_text()) if restore else None
    if old:
        # Verify the old object BEFORE calling an endpoint that could regenerate it.
        previous = lab.client("s3").get_object(Bucket=old["bucket"], Key=old["key"])["Body"].read()
        if hashlib.sha256(previous).hexdigest() != old["sha256"]:
            raise AssertionError("Original S3 PDF did not survive restart")
    request = old["request"] if old else {"externalReference":"local-" + str(uuid.uuid4()), "customerName":"Local diagnostic", "customerEmail":"local@example.com", "productCode":"AUTO-01"}
    def post(path, data): return http(base + "/api/v1/" + path, data)
    # API proxy can expose actuator too; an API base URL includes its local gateway prefix.
    if http(base + "/actuator/health")["status"] != "UP": raise AssertionError("Micro not healthy")
    sale = post("endpoint0/sales", request)
    if old and sale["saleId"] != old["saleId"]: raise AssertionError("Sale did not survive restart")
    sid = sale["saleId"]
    if post("endpoint0/sales", request)["saleId"] != sid: raise AssertionError("External reference not idempotent")
    doc = post("endpoint1/documents", {"saleId":sid})
    policy = post("endpoint2/policies", {"saleId":sid})
    if not restore:
        try: post("endpoint4/policies/complete", {"saleId":sid,"policyId":policy["policyId"]})
        except urllib.error.HTTPError as exc:
            if exc.code != 400 or json.load(exc).get("error") != "INVALID_WORKFLOW_STATE": raise
        else: raise AssertionError("Premature completion did not fail")
    for path, body, expected in [
        ("endpoint3/documents/upload", {"saleId":sid,"documentId":doc["documentId"]}, "UPLOADED"),
        ("endpoint4/policies/complete", {"saleId":sid,"policyId":policy["policyId"]}, "COMPLETED"),
        ("endpoint5/documents/release", {"saleId":sid,"documentId":doc["documentId"]}, "RELEASED")]:
        response = post(path,body)
        if response["status"] != expected or response["saleId"] != sid: raise AssertionError(response)
    bucket = lab.outputs("storage")["BucketName"]
    key = f"sales/{sid}/{doc['documentId']}.pdf"
    value = lab.client("s3").get_object(Bucket=bucket, Key=key)
    pdf = value["Body"].read()
    if value["ContentType"] != "application/pdf" or not pdf.startswith(b"%PDF-"):
        raise AssertionError("Invalid PDF in S3")
    download = response["downloadUrl"]
    public = urlsplit(download)
    endpoint_url(f"{public.scheme}://{public.netloc}")
    # Signed host must remain unchanged. Reachability is part of the test.
    if http(download) != pdf: raise AssertionError("Signed download differs from S3 PDF")
    result = {"request":request,"saleId":sid,"documentId":doc["documentId"],"policyId":policy["policyId"],"key":key,"sha256":hashlib.sha256(pdf).hexdigest(),"bucket":bucket}
    if old and any(result[k] != old[k] for k in ("documentId","policyId","key","sha256")):
        raise AssertionError("DB/S3 persistence mismatch")
    save(record,result)
    save(ROOT / "reports/http.json", {"status":"PASS", "restore":restore, **result})
    print(json.dumps(result,indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--endpoint",required=True,type=endpoint_url)
    p.add_argument("command",choices=["iam","queues","micro","restore"])
    p.add_argument("--micro-url")
    a=p.parse_args()
    lab=Lab(a.endpoint)
    try:
        if a.command == "iam": iam(lab)
        elif a.command == "queues": queues(lab)
        else: micro(lab,a.micro_url,a.command=="restore")
    except Exception as exc:
        save(ROOT / "reports" / (a.command + ".json"), {"status":"FAIL", "error":str(exc), "endpoint":lab.endpoint})
        raise


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        print(f"FAIL: {exc}",file=sys.stderr)
        sys.exit(1)
