"""Shared runtime: explicit LocalStack endpoint, no proxy, redirect or AWS fallback."""
import ipaddress
import json
import os
import urllib.parse
import urllib.request

import boto3
from botocore.config import Config


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Redirect forbidden")


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def local_url(value, port):
    url = urllib.parse.urlsplit(value)
    allowed = url.hostname in {"localhost.localstack.cloud", "localhost", "127.0.0.1", "100.100.154.68"}
    try:
        address = ipaddress.ip_address(url.hostname)
        allowed = allowed or any(address in ipaddress.ip_network(net) for net in
                                 ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    except ValueError:
        pass
    if not allowed or url.scheme != "http" or url.port != port or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("Only explicit local runtime URLs allowed")
    return value.rstrip("/")


_verified = None


def client(service):
    global _verified
    endpoint = local_url(os.environ["LAB_AWS_ENDPOINT"], 4566)
    if _verified != endpoint:
        with OPENER.open(endpoint + "/_localstack/health", timeout=5) as response:
            health = json.load(response)
        if not health.get("version") or not isinstance(health.get("services"), dict):
            raise ValueError("Runtime endpoint is not LocalStack")
        _verified = endpoint
    # Lambda-injected role credentials only; never use default providers/host profiles.
    result = boto3.client(service, endpoint_url=endpoint, region_name="us-east-1",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
        config=Config(connect_timeout=5, read_timeout=15, proxies={}, retries={"max_attempts": 1},
                      s3={"addressing_style": "path"}))

    def guard(request, **kwargs):
        actual = urllib.parse.urlsplit(request.url)
        expected = urllib.parse.urlsplit(endpoint)
        if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
            raise ValueError("AWS endpoint rewrite forbidden")
    result.meta.events.register("before-send.*.*", guard)
    return result


def secret(arn):
    if not arn.startswith("arn:aws:secretsmanager:us-east-1:000000000000:secret:micro-test-local-"):
        raise ValueError("Unowned secret")
    return json.loads(client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"])
