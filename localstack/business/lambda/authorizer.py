"""Disposable lab JWT, HS256; issuer/audience/expiry checked, cache disabled in API."""
import base64
import hashlib
import hmac
import json
import os
import time
from runtime import secret


def decode(value):
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def verify(token, key, now=None):
    if len(token) > 4096:
        raise ValueError("Token too large")
    header, payload, signature = token.split(".")
    algorithm = json.loads(decode(header))
    if algorithm != {"alg": "HS256", "typ": "JWT"}:
        raise ValueError("Unsupported JWT header")
    expected = hmac.new(key.encode(), (header + "." + payload).encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, decode(signature)):
        raise ValueError("Invalid signature")
    claims = json.loads(decode(payload))
    now = int(time.time()) if now is None else now
    if (claims.get("iss") != "micro-test-local" or claims.get("aud") != "micro-test-local-purchases"
            or not isinstance(claims.get("sub"), str) or not claims["sub"]
            or type(claims.get("exp")) is not int or claims["exp"] <= now
            or type(claims.get("iat")) is not int or claims["iat"] > now
            or claims["exp"] - claims["iat"] > 3600):
        raise ValueError("Invalid claims")
    return claims


def handler(event, context):
    try:
        value = event.get("authorizationToken", "")
        if not value.startswith("Bearer "):
            raise ValueError("Missing bearer token")
        claims = verify(value[7:], secret(os.environ["AUTH_SECRET_ARN"])["key"])
        arn = event["methodArn"]
        if not arn.startswith("arn:aws:execute-api:us-east-1:000000000000:"):
            raise ValueError("Unexpected API account")
        return {"principalId": claims["sub"], "policyDocument": {"Version": "2012-10-17", "Statement": [
            {"Action": "execute-api:Invoke", "Effect": "Allow", "Resource": arn}]},
            "context": {"subject": claims["sub"]}}
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise Exception("Unauthorized") from None
