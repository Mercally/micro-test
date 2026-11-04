"""Real PostgreSQL + mocked SQS boundary. Not an end-to-end infrastructure claim."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / "lambda"), str(BASE.parent / ".state/business/dependencies")]
import pg8000.dbapi
import publisher


@unittest.skipUnless(os.environ.get("LAB_TEST_POSTGRES") == "true", "Isolated PostgreSQL required")
class PublisherPostgresTest(unittest.TestCase):
    def connection(self):
        return pg8000.dbapi.connect(host="127.0.0.1", port=int(os.environ.get("LAB_TEST_PG_PORT", "15432")),
                                    user="docs", password="docs", database="docs_business_test", timeout=10)

    def setUp(self):
        self.db = self.connection()
        cursor = self.db.cursor()
        cursor.execute("SELECT current_database()")
        self.assertEqual(cursor.fetchone()[0], "docs_business_test")
        cursor.execute("TRUNCATE endpoint0.lab_faults, endpoint0.outbox, endpoint0.sales CASCADE")
        self.db.commit()
        self.messages = []
        self.lock = threading.Lock()
        self.patches = [patch.object(publisher, "connect", self.connection), patch.object(publisher, "client", return_value=self),
            patch.dict(os.environ, {"LAB_AWS_ENDPOINT": "http://127.0.0.1:4566", "QUEUE_URL": "http://127.0.0.1:4566/000000000000/micro-test-local-purchases"})]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.db.close()

    def send_message(self, **kwargs):
        with self.lock: self.messages.append(json.loads(kwargs["MessageBody"]))
        time.sleep(0.03)
        return {"MessageId": str(uuid.uuid4())}

    def seed(self, count=1):
        cursor = self.db.cursor()
        events = []
        for _ in range(count):
            sale, event = str(uuid.uuid4()), str(uuid.uuid4())
            payload = {"eventId": event, "saleId": sale, "correlationId": sale, "type": "PurchaseAccepted", "version": 1, "generation": 1}
            cursor.execute("INSERT INTO endpoint0.sales(id,external_reference,customer_name,customer_email,product_code,status) VALUES (%s,%s,'Local','local@example.com','AUTO','ACCEPTED')", (sale, sale))
            cursor.execute("INSERT INTO endpoint0.outbox(event_id,sale_id,generation,event_type,version,payload) VALUES (%s,%s,1,'PurchaseAccepted',1,%s::jsonb)", (event, sale, json.dumps(payload)))
            events.append(event)
        self.db.commit()
        return events

    def test_concurrent_publishers_lock_disjoint_rows(self):
        expected = self.seed(20)
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: publisher.handler({"limit": 20}, None), range(4)))
        self.assertCountEqual([m["eventId"] for m in self.messages], expected)
        cursor = self.db.cursor()
        cursor.execute("SELECT count(*) FROM endpoint0.outbox WHERE published_at IS NOT NULL")
        self.assertEqual(cursor.fetchone()[0], 20)

    def test_crash_after_send_keeps_pending_then_redelivers_same_event(self):
        event = self.seed()[0]
        with self.assertRaisesRegex(RuntimeError, "LAB_CRASH_AFTER_SEND"):
            publisher.handler({"eventId": event, "crashAfterSendEventId": event}, None)
        cursor = self.db.cursor()
        cursor.execute("SELECT published_at,attempts,last_error FROM endpoint0.outbox WHERE event_id=%s", (event,))
        row = cursor.fetchone()
        self.assertIsNone(row[0]); self.assertEqual(row[1], 1); self.assertIn("LAB_CRASH_AFTER_SEND", row[2])
        cursor.execute("UPDATE endpoint0.outbox SET available_at=now() WHERE event_id=%s", (event,))
        self.db.commit()
        publisher.handler({"eventId": event}, None)
        self.assertEqual([m["eventId"] for m in self.messages], [event, event])
        cursor.execute("SELECT published_at,attempts FROM endpoint0.outbox WHERE event_id=%s", (event,))
        row = cursor.fetchone()
        self.assertIsNotNone(row[0]); self.assertEqual(row[1], 2)

    def test_sqs_failure_never_marks_published(self):
        self.seed()
        with patch.object(self, "send_message", side_effect=RuntimeError("SQS unavailable")):
            with self.assertRaisesRegex(RuntimeError, "SQS unavailable"): publisher.handler({}, None)
        cursor = self.db.cursor()
        cursor.execute("SELECT published_at,attempts FROM endpoint0.outbox")
        row = cursor.fetchone()
        self.assertIsNone(row[0]); self.assertEqual(row[1], 1)


if __name__ == "__main__": unittest.main()
