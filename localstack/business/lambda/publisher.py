"""Transactional outbox, at-least-once SQS delivery. Row lock held through SendMessage."""
import json
import os
import uuid
import pg8000.dbapi
from runtime import client, secret


def connect():
    credentials = secret(os.environ["DATABASE_SECRET_ARN"])
    return pg8000.dbapi.connect(host=os.environ["DATABASE_HOST"], port=int(os.environ["DATABASE_PORT"]),
        database=credentials["dbname"], user=credentials["username"], password=credentials["password"], timeout=8)


def handler(event, context):
    db = connect()
    sent = []
    try:
        if event.get("health"):
            cursor = db.cursor()
            cursor.execute("SELECT current_database(), current_user")
            database = cursor.fetchone()
            cursor.execute("SELECT to_regclass('endpoint0.sales') IS NOT NULL, to_regclass('endpoint0.outbox') IS NOT NULL")
            tables = cursor.fetchone()
            return {"database": database, "healthy": True, "schemaReady": all(tables),
                    "tables": {"sales": tables[0], "outbox": tables[1]}}
        sqs = client("sqs")
        queue = os.environ["QUEUE_URL"]
        # Replace response-provided host with our verified gateway; path stays owned.
        from urllib.parse import urlsplit
        path = urlsplit(queue).path
        if not path.endswith("/micro-test-local-purchases") or "/000000000000/" not in path:
            raise ValueError("Unowned queue")
        queue = os.environ["LAB_AWS_ENDPOINT"].rstrip("/") + path
        target = event.get("eventId")
        if target:
            target = str(uuid.UUID(target))
        for _ in range(min(max(int(event.get("limit", 20)), 1), 50)):
            cursor = db.cursor()
            cursor.execute("""
                SELECT event_id,payload FROM endpoint0.outbox
                WHERE published_at IS NULL AND available_at<=now()
                  AND (%s::uuid IS NULL OR event_id=%s::uuid)
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
                """, (target, target))
            row = cursor.fetchone()
            if not row:
                db.rollback()
                break
            event_id, payload = str(row[0]), row[1]
            try:
                response = sqs.send_message(QueueUrl=queue, MessageBody=json.dumps(payload),
                    MessageAttributes={"correlationId": {"DataType": "String", "StringValue": payload["correlationId"]},
                                       "eventId": {"DataType": "String", "StringValue": event_id}})
                if event.get("crashAfterSendEventId") == event_id:
                    raise RuntimeError("LAB_CRASH_AFTER_SEND: delivery confirmed, transaction deliberately rolled back")
                cursor.execute("""
                    UPDATE endpoint0.outbox SET published_at=now(),sqs_message_id=%s,
                      attempts=attempts+1,last_error=NULL WHERE event_id=%s
                    """, (response["MessageId"], event_id))
                db.commit()
                sent.append({"eventId": event_id, "messageId": response["MessageId"]})
                print(json.dumps({"event": "outbox_published", "eventId": event_id, "correlationId": payload["correlationId"]}))
            except Exception as exc:
                db.rollback()
                cursor = db.cursor()
                cursor.execute("""
                    UPDATE endpoint0.outbox SET attempts=attempts+1,last_error=%s,
                      available_at=now()+interval '5 seconds'
                    WHERE event_id=%s AND published_at IS NULL
                    """, (str(exc)[:1000], event_id))
                db.commit()
                raise
        return {"published": sent}
    finally:
        db.close()
