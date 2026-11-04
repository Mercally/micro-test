"""Only the explicitly selected LocalStack gateway may receive AWS requests."""
import ipaddress
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "micro-test-local-"
REGION = "us-east-1"
ACCOUNT = "000000000000"
TAG = {"Key": "Lab", "Value": "micro-test-local"}
# Explicit lab hosts only. The laptop address comes from the user-selected
# localstack-laptop profile; this does not authorize the entire Tailscale range.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "100.95.224.37", "100.100.154.68"})


def endpoint_url(value):
    url = urllib.parse.urlsplit(value)
    if (url.scheme != "http" or url.hostname not in LOCAL_HOSTS
            or url.port != 4566 or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("Endpoint must be http://{127.0.0.1,localhost,[::1],100.95.224.37,100.100.154.68}:4566")
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("HTTP redirects are forbidden in this lab")


def http(url, data=None, method=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {"Content-Type": "application/json"} if data is not None else {}
    payload = json.dumps(data).encode() if data is not None else None
    with opener.open(urllib.request.Request(url, payload, headers, method=method), timeout=20) as r:
        body = r.read()
        return json.loads(body) if "json" in r.headers.get("Content-Type", "") else body


class Lab:
    def __init__(self, endpoint):
        self.endpoint = endpoint_url(endpoint)
        health = http(self.endpoint + "/_localstack/health")
        info = http(self.endpoint + "/_localstack/info")
        if not (isinstance(health.get("services"), dict) and health.get("version")
                and info.get("edition") in ("community", "pro") and info.get("version")):
            raise ValueError("Endpoint did not identify itself as LocalStack")
        self.health = health
        self.issued_credentials = set()
        # Deliberately never store the environment or license token returned by internal APIs.
        self.info = {k: info.get(k) for k in ("version", "edition", "is_license_activated", "system")}
        for key in list(os.environ):
            if key.startswith("AWS_") or key in ("BOTO_CONFIG", "LOCALSTACK_AUTH_TOKEN"):
                os.environ.pop(key)
        os.environ.update(AWS_EC2_METADATA_DISABLED="true", AWS_CONFIG_FILE=os.devnull,
                          AWS_SHARED_CREDENTIALS_FILE=os.devnull, BOTO_CONFIG=os.devnull)
        import boto3
        self.session = boto3.Session(aws_access_key_id="test", aws_secret_access_key="test",
                                     region_name=REGION)
        identity = self.client("sts").get_caller_identity()
        if identity["Account"] != ACCOUNT:
            raise ValueError("Unexpected account; lab requires local test account 000000000000")

    def client(self, service, credentials=None):
        from botocore.config import Config
        kwargs = dict(endpoint_url=self.endpoint, region_name=REGION,
                      config=Config(connect_timeout=5, read_timeout=30, proxies={},
                                    retries={"max_attempts": 1}, s3={"addressing_style": "path"}))
        if credentials:
            key, secret = credentials
            if (key, secret) not in self.issued_credentials:
                raise ValueError("Restricted credentials must have been minted by this verified LocalStack session")
            kwargs.update(aws_access_key_id=key, aws_secret_access_key=secret, aws_session_token=None)
        client = self.session.client(service, **kwargs)
        # Also reject SDK endpoint rewrites, S3 redirects, or URLs supplied by an API response.
        def guard(request, **unused):
            actual = urllib.parse.urlsplit(request.url)
            expected = urllib.parse.urlsplit(self.endpoint)
            if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
                raise ValueError("SDK attempted to leave the explicit LocalStack gateway")
        client.meta.events.register("before-send.*.*", guard)
        return client

    def create_local_key(self, user):
        if not user.startswith(PREFIX + "iam-probe-"):
            raise ValueError("Only temporary lab identities may receive test keys")
        value = self.client("iam").create_access_key(UserName=user)["AccessKey"]
        self.issued_credentials.add((value["AccessKeyId"], value["SecretAccessKey"]))
        return value

    def stack(self, name):
        from botocore.exceptions import ClientError
        if not name.startswith(PREFIX):
            raise ValueError("Stack outside local prefix")
        try:
            stack = self.client("cloudformation").describe_stacks(StackName=name)["Stacks"][0]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ValidationError" and "does not exist" in str(exc):
                return None
            raise
        if TAG not in stack.get("Tags", []):
            raise ValueError(f"Refusing unowned stack: {name}")
        return stack

    def outputs(self, suffix):
        stack = self.stack(PREFIX + suffix)
        if not stack:
            raise ValueError(f"Missing stack: {suffix}")
        return {x["OutputKey"]: x["OutputValue"] for x in stack.get("Outputs", [])}


def save(path, data):
    path = Path(path)
    if not path.resolve().is_relative_to(ROOT):
        raise ValueError("Generated files must remain under localstack/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")
    path.chmod(0o600)
