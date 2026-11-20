package com.example.docssimulator.service;

import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

import software.amazon.awssdk.core.exception.SdkException;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.SendMessageRequest;

/**
 * Worker (N06): transactional outbox → SQS, at-least-once. The row lock is held through SendMessage;
 * a crash after send and before commit republishes, which the idempotent steps absorb.
 */
@Component
@ConditionalOnProperty(name = "app.worker.enabled", havingValue = "true")
public class OutboxPublisher {
    private static final Logger log = LoggerFactory.getLogger(OutboxPublisher.class);

    private final JdbcTemplate db;
    private final TransactionTemplate tx;
    private final SqsClient sqs;
    private final String queueUrl;
    private final int batch;

    public OutboxPublisher(JdbcTemplate db, TransactionTemplate tx, SqsClient sqs,
                           @Value("${app.worker.queue-url}") String queueUrl,
                           @Value("${app.worker.batch:20}") int batch) {
        if (queueUrl.isBlank() || batch < 1) throw new IllegalArgumentException("Worker requiere QUEUE_URL y batch >= 1");
        this.db = db;
        this.tx = tx;
        this.sqs = sqs;
        this.queueUrl = queueUrl;
        this.batch = batch;
    }

    @Scheduled(fixedDelayString = "${app.worker.poll-ms:1000}")
    public void publish() {
        for (int i = 0; i < batch; i++) {
            if (!Boolean.TRUE.equals(tx.execute(status -> publishOne()))) return;
        }
    }

    private boolean publishOne() {
        var rows = db.queryForList("""
                SELECT event_id, payload::text AS payload FROM endpoint0.outbox
                WHERE published_at IS NULL AND available_at<=now()
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
                """);
        if (rows.isEmpty()) return false;
        UUID eventId = (UUID) rows.get(0).get("event_id");
        String payload = rows.get(0).get("payload").toString();
        try {
            String messageId = sqs.sendMessage(SendMessageRequest.builder().queueUrl(queueUrl).messageBody(payload).build())
                    .messageId();
            db.update("UPDATE endpoint0.outbox SET published_at=now(),sqs_message_id=?,attempts=attempts+1,last_error=NULL WHERE event_id=?",
                    messageId, eventId);
            log.info("outbox_published eventId={} messageId={}", eventId, messageId);
            return true;
        } catch (SdkException exception) {
            String error = String.valueOf(exception.getMessage());
            db.update("""
                    UPDATE endpoint0.outbox SET attempts=attempts+1,last_error=?,available_at=now()+interval '5 seconds'
                    WHERE event_id=?
                    """, error.substring(0, Math.min(1000, error.length())), eventId);
            log.warn("outbox_publish_failed eventId={} error={}", eventId, error);
            return false;
        }
    }
}
