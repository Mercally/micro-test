package com.example.docssimulator.service;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

import com.example.docssimulator.api.Requests;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.support.TransactionTemplate;

import software.amazon.awssdk.core.exception.SdkClientException;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.SendMessageRequest;
import software.amazon.awssdk.services.sqs.model.SendMessageResponse;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

/**
 * Outbox worker against real PostgreSQL (locks, SKIP LOCKED, transactions); SQS is a fake.
 * Proves: every accepted sale is sent once, failures keep the event pending with backoff,
 * and concurrent worker pods never send the same event twice.
 */
@SpringBootTest(properties = {"app.simulator.failure-probability=0", "app.lab.purchases-enabled=true",
        "app.s3.endpoint=http://127.0.0.1:4566", "logging.level.root=WARN"})
@EnabledIfEnvironmentVariable(named = "LAB_TEST_POSTGRES", matches = "true")
class OutboxPublisherIntegrationTest {
    static final String QUEUE = "https://sqs.us-east-1.amazonaws.com/000000000000/micro-test-purchases";

    @Autowired PurchaseService purchases;
    @Autowired JdbcTemplate db;
    @Autowired TransactionTemplate tx;
    @Autowired ObjectMapper json;
    @MockitoBean S3Storage storage;

    SqsClient sqs;
    final List<SendMessageRequest> sent = new ArrayList<>();
    final AtomicInteger ids = new AtomicInteger();

    @BeforeEach
    void setUp() {
        assertEquals("docs_business_test", db.queryForObject("SELECT current_database()", String.class),
                "Tests must never truncate a lab/user database");
        db.execute("TRUNCATE process.steps,endpoint0.lab_faults,endpoint0.outbox,endpoint0.sales CASCADE");
        sqs = mock(SqsClient.class);
        when(sqs.sendMessage(any(SendMessageRequest.class))).thenAnswer(i -> {
            synchronized (sent) { sent.add(i.getArgument(0)); }
            return SendMessageResponse.builder().messageId("msg-" + ids.incrementAndGet()).build();
        });
    }

    OutboxPublisher worker(int batch) {
        return new OutboxPublisher(db, tx, sqs, QUEUE, batch);
    }

    UUID accept(String reference) {
        var sale = purchases.accept(new Requests.RegisterSaleRequest(reference, "Lab", "lab@example.com", "AUTO-01"), null);
        return UUID.fromString(sale.get("saleId").toString());
    }

    Map<String, Object> outbox(UUID saleId) {
        return db.queryForMap("SELECT * FROM endpoint0.outbox WHERE sale_id=?", saleId);
    }

    @Test
    void publishesEachPendingEventOnceWithContractPayload() throws Exception {
        UUID first = accept("w-1");
        UUID second = accept("w-2");
        worker(20).publish();
        worker(20).publish(); // nothing left: no resend

        assertEquals(2, sent.size());
        for (var request : sent) {
            assertEquals(QUEUE, request.queueUrl());
            var body = json.readTree(request.messageBody());
            for (String field : new String[]{"saleId", "eventId", "correlationId"}) assertTrue(body.hasNonNull(field), field);
        }
        assertEquals(Set.of(first.toString(), second.toString()),
                Set.of(json.readTree(sent.get(0).messageBody()).get("saleId").asText(),
                       json.readTree(sent.get(1).messageBody()).get("saleId").asText()));
        var row = outbox(first);
        assertNotNull(row.get("published_at"));
        assertNotNull(row.get("sqs_message_id"));
        assertEquals(1, ((Number) row.get("attempts")).intValue());
        assertNull(row.get("last_error"));
    }

    @Test
    void sendFailureKeepsEventPendingWithBackoffThenSucceeds() {
        UUID saleId = accept("w-fail");
        when(sqs.sendMessage(any(SendMessageRequest.class))).thenThrow(SdkClientException.create("sqs down"));
        worker(20).publish();

        var row = outbox(saleId);
        assertNull(row.get("published_at"), "failed send must not mark published");
        assertEquals(1, ((Number) row.get("attempts")).intValue());
        assertTrue(row.get("last_error").toString().contains("sqs down"));

        worker(20).publish(); // inside the 5 s backoff window: not retried yet
        verify(sqs, times(1)).sendMessage(any(SendMessageRequest.class));

        db.update("UPDATE endpoint0.outbox SET available_at=now() WHERE sale_id=?", saleId); // backoff elapsed
        reset(sqs);
        when(sqs.sendMessage(any(SendMessageRequest.class)))
                .thenReturn(SendMessageResponse.builder().messageId("msg-ok").build());
        worker(20).publish();
        row = outbox(saleId);
        assertEquals("msg-ok", row.get("sqs_message_id"));
        assertEquals(2, ((Number) row.get("attempts")).intValue());
        assertNull(row.get("last_error"));
    }

    @Test
    void batchLimitsEventsPerCycle() {
        for (int i = 0; i < 3; i++) accept("w-batch-" + i);
        worker(2).publish();
        assertEquals(2, sent.size());
        worker(2).publish();
        assertEquals(3, sent.size());
    }

    @Test
    void concurrentWorkerPodsNeverSendTheSameEventTwice() throws Exception {
        for (int i = 0; i < 12; i++) accept("w-conc-" + i);
        Set<String> eventIds = ConcurrentHashMap.newKeySet();
        when(sqs.sendMessage(any(SendMessageRequest.class))).thenAnswer(i -> {
            SendMessageRequest request = i.getArgument(0);
            assertTrue(eventIds.add(json.readTree(request.messageBody()).get("eventId").asText()), "duplicate send");
            Thread.sleep(20); // hold the row lock so workers overlap
            synchronized (sent) { sent.add(request); }
            return SendMessageResponse.builder().messageId("msg-" + ids.incrementAndGet()).build();
        });
        var executor = Executors.newFixedThreadPool(4);
        try {
            var pods = new ArrayList<Callable<Void>>();
            for (int i = 0; i < 4; i++) pods.add(() -> { worker(20).publish(); return null; });
            for (var result : executor.invokeAll(pods)) result.get();
        } finally {
            executor.shutdownNow();
        }
        assertEquals(12, sent.size());
        assertEquals(0, db.queryForObject("SELECT count(*) FROM endpoint0.outbox WHERE published_at IS NULL", Integer.class));
    }

    @Test
    void rejectsMissingQueueUrl() {
        assertThrows(IllegalArgumentException.class, () -> new OutboxPublisher(db, tx, sqs, " ", 20));
        assertThrows(IllegalArgumentException.class, () -> new OutboxPublisher(db, tx, sqs, QUEUE, 0));
    }
}
