"""Integration fixture only. This is not a sales publisher or an authorizer."""
import json


def handler(event, context):
    api = isinstance(event, dict) and "httpMethod" in event
    payload = json.loads(event.get("body") or "{}") if api else event
    records = payload if isinstance(payload, list) else [payload]
    messages = [json.loads(r["body"]) if "body" in r else r for r in records]
    if not messages or any(not m.get("diagnosticId") for m in messages):
        raise ValueError("diagnosticId required; this fixture does not process sales")
    result = {"diagnostic": True, "processedBy": "micro-test-local-diagnostic",
              "messages": messages}
    print(json.dumps(result))
    if api:
        return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
                "body": json.dumps(result)}
    return result
