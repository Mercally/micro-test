"""Local HTTP adapter only. Business rules remain in the Java micro."""
import json
import os
import urllib.error
import urllib.request
import uuid
from runtime import OPENER, local_url


class InvalidEvent(Exception): pass
class TransientStepError(Exception): pass
class PermanentStepError(Exception): pass


def normalize(value, owner):
    try:
        if not isinstance(value, list) or len(value) != 1:
            raise ValueError("Expected one-record Pipe SQS batch")
        record = value[0]
        if not isinstance(record, dict) or record.get("eventSource") != "aws:sqs":
            raise ValueError("Expected SQS eventSource")
        body = json.loads(record["body"])
        if not isinstance(body, dict) or body.get("type") != "PurchaseAccepted" or type(body.get("version")) is not int or body["version"] != 1:
            raise ValueError("Unsupported event contract")
        for key in ("eventId", "saleId", "correlationId"):
            body[key] = str(uuid.UUID(body[key]))
        if body["correlationId"] != body["saleId"] or type(body.get("generation")) is not int or body["generation"] < 1:
            raise ValueError("Invalid correlation/generation")
        return {key: body[key] for key in ("eventId", "saleId", "correlationId", "generation")} | {"owner": owner}
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidEvent(str(exc)) from None


def handler(event, context):
    operation = event["operation"]
    if operation == "health":
        url = local_url(os.environ["MICRO_BASE_URL"], 31080) + "/actuator/health"
        with OPENER.open(url, timeout=12) as response:
            result = json.load(response)
        if result.get("status") != "UP":
            raise TransientStepError("Micro not UP")
        return result
    if operation == "normalize":
        return normalize(event["input"], event["owner"])
    state = event["context"]
    body = {"eventId": state["eventId"], "owner": state["owner"], "operation": operation}
    if "document" in state:
        body["documentId"] = state["document"]["documentId"]
    if "policy" in state:
        body["policyId"] = state["policy"]["policyId"]
    if operation == "fail":
        body["error"] = json.dumps(state.get("failure", {"Error": "WORKFLOW_FAILED"}))[:4000]
    url = local_url(os.environ["MICRO_BASE_URL"], 31080) + "/api/v1/internal/purchases/" + str(uuid.UUID(state["saleId"])) + "/run"
    request = urllib.request.Request(url, json.dumps(body).encode(),
        {"Content-Type": "application/json", "X-Correlation-Id": state["correlationId"]}, method="POST")
    try:
        with OPENER.open(request, timeout=12) as response:
            result = json.load(response)
        print(json.dumps({"eventId": state["eventId"], "saleId": state["saleId"], "operation": operation, "ok": True}))
        return result
    except urllib.error.HTTPError as exc:
        message = f"HTTP {exc.code}: " + exc.read(2000).decode(errors="replace")
        error = TransientStepError if exc.code >= 500 or exc.code == 429 else PermanentStepError
        raise error(message) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise TransientStepError(str(exc)) from None
