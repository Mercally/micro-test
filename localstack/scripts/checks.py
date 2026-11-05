#!/usr/bin/env python3
"""Runtime checks. A failed or unreachable check never counts as passed."""
import json
import uuid
from botocore.exceptions import ClientError
from common import ROOT, PREFIX, save


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
