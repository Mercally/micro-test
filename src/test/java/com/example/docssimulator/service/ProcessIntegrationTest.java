package com.example.docssimulator.service;

import java.util.ArrayList;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

import com.example.docssimulator.api.PurchaseException;
import com.example.docssimulator.api.Requests;
import com.example.docssimulator.api.TransientWorkflowException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * Redelivery contract of the AWS flow (N02) against real PostgreSQL: any step, called any number
 * of times by any execution, never repeats work nor leaves inconsistent state.
 * S3 and CM/insurer are in-memory fakes that can lose acknowledgements.
 */
@SpringBootTest(properties = {"app.simulator.failure-probability=0", "app.lab.purchases-enabled=true",
        "app.s3.endpoint=http://127.0.0.1:4566", "logging.level.root=WARN"})
@EnabledIfEnvironmentVariable(named = "LAB_TEST_POSTGRES", matches = "true")
class ProcessIntegrationTest {
    @Autowired ProcessService process;
    @Autowired PurchaseService purchases;
    @Autowired JdbcTemplate db;
    @MockitoBean S3Storage storage;
    @MockitoBean ExternalSystems external;

    final Map<String, byte[]> s3 = new ConcurrentHashMap<>();
    final Set<UUID> cm = ConcurrentHashMap.newKeySet();
    final Set<UUID> insurer = ConcurrentHashMap.newKeySet();
    final AtomicInteger uploads = new AtomicInteger();
    final AtomicInteger cmStores = new AtomicInteger();
    final AtomicInteger insurerSends = new AtomicInteger();
    final AtomicInteger lostAcks = new AtomicInteger();

    @BeforeEach
    void setUp() {
        assertEquals("docs_business_test", db.queryForObject("SELECT current_database()", String.class),
                "Tests must never truncate a lab/user database");
        db.execute("TRUNCATE process.steps,endpoint0.lab_faults,endpoint0.outbox,endpoint0.sales CASCADE");
        doAnswer(i -> { uploads.incrementAndGet(); s3.put(i.getArgument(0), i.getArgument(1)); return null; })
                .when(storage).upload(anyString(), any());
        when(storage.exists(anyString())).thenAnswer(i -> s3.containsKey(i.<String>getArgument(0)));
        when(storage.download(anyString())).thenAnswer(i -> s3.get(i.<String>getArgument(0)));
        doAnswer(i -> s3.remove(i.<String>getArgument(0))).when(storage).delete(anyString());
        when(external.findCmDocument(any())).thenAnswer(i -> cm.contains(i.<UUID>getArgument(0)) ? "CM-x" : null);
        when(external.storeCmDocument(any(), any())).thenAnswer(i -> {
            cmStores.incrementAndGet();
            cm.add(i.getArgument(0));
            if (lostAcks.getAndDecrement() > 0) throw new TransientWorkflowException("cm-ack-lost");
            return "CM-x";
        });
        when(external.findInsurerPolicy(any())).thenAnswer(i -> insurer.contains(i.<UUID>getArgument(0)) ? "POL-x" : null);
        when(external.sendInsurerPolicy(any())).thenAnswer(i -> {
            insurerSends.incrementAndGet();
            insurer.add(i.getArgument(0));
            if (lostAcks.getAndDecrement() > 0) throw new TransientWorkflowException("insurer-ack-lost");
            return "POL-x";
        });
    }

    UUID sale(String reference) {
        var accepted = purchases.accept(new Requests.RegisterSaleRequest(reference, "Lab", "lab@example.com", "AUTO-01"), null);
        return UUID.fromString(accepted.get("saleId").toString());
    }

    void runAll(UUID saleId) {
        process.document(saleId, null);
        process.cm(saleId, null);
        process.insurer(saleId, null);
    }

    String saleStatus(UUID saleId) {
        return db.queryForObject("SELECT status FROM endpoint0.sales WHERE id=?", String.class, saleId);
    }

    @Test
    void redeliveredMessageRepeatsNoWork() {
        UUID saleId = sale("redelivery");
        runAll(saleId);
        runAll(saleId); // new execution after visibility timeout / duplicate SQS delivery
        runAll(saleId);
        assertEquals(1, uploads.get());
        assertEquals(1, cmStores.get());
        assertEquals(1, insurerSends.get());
        assertEquals("COMPLETED", saleStatus(saleId));
        assertEquals("ALREADY_DONE", process.insurer(saleId, null).get("outcome"));
    }

    @Test
    void lostAcknowledgementIsReconciledNotResent() {
        UUID saleId = sale("lost-ack");
        process.document(saleId, null);
        lostAcks.set(1);
        assertThrows(TransientWorkflowException.class, () -> process.cm(saleId, null));
        assertEquals("RECONCILED", process.cm(saleId, null).get("outcome"));
        lostAcks.set(1);
        assertThrows(TransientWorkflowException.class, () -> process.insurer(saleId, null));
        assertEquals("PROCESSING", saleStatus(saleId), "failed step must not change sale state");
        assertEquals("RECONCILED", process.insurer(saleId, null).get("outcome"));
        assertEquals(1, cmStores.get());
        assertEquals(1, insurerSends.get());
        assertEquals("COMPLETED", saleStatus(saleId));
    }

    @Test
    void concurrentExecutionsOfSameSaleSerialize() throws Exception {
        UUID saleId = sale("concurrent");
        var executor = Executors.newFixedThreadPool(6);
        try {
            var calls = new ArrayList<Callable<Void>>();
            for (int i = 0; i < 6; i++) calls.add(() -> { runAll(saleId); return null; });
            for (var result : executor.invokeAll(calls)) result.get();
        } finally {
            executor.shutdownNow();
        }
        assertEquals(1, uploads.get());
        assertEquals(1, cmStores.get());
        assertEquals(1, insurerSends.get());
        assertEquals(3, db.queryForObject("SELECT count(*) FROM process.steps WHERE sale_id=?", Integer.class, saleId));
    }

    @Test
    void stepsOutOfOrderAndPrematureCleanupAreRejectedWithoutSideEffects() {
        UUID saleId = sale("order");
        var outOfOrder = assertThrows(PurchaseException.class, () -> process.insurer(saleId, null));
        assertEquals(409, outOfOrder.status());
        process.document(saleId, null);
        assertEquals(409, assertThrows(PurchaseException.class, () -> process.cleanup(saleId)).status());
        assertEquals(1, s3.size(), "temporary file kept until completion");
        assertEquals(0, insurerSends.get());
    }

    @Test
    void cleanupAfterCompletionIsIdempotentAndRedeliveryStillSucceeds() {
        UUID saleId = sale("cleanup");
        runAll(saleId);
        process.cleanup(saleId);
        process.cleanup(saleId);
        assertTrue(s3.isEmpty());
        runAll(saleId); // DeleteMessage failed after cleanup: redelivery must not regenerate
        assertEquals(1, uploads.get());
    }

    @Test
    void missingTemporaryFileIsRegeneratedOnNextDelivery() {
        UUID saleId = sale("regenerate");
        process.document(saleId, null);
        s3.clear();
        assertEquals(409, assertThrows(PurchaseException.class, () -> process.cm(saleId, null)).status());
        assertEquals("DONE", process.document(saleId, null).get("outcome"));
        runAll(saleId);
        assertEquals(2, uploads.get());
        assertEquals("COMPLETED", saleStatus(saleId));
    }

    @Test
    void finalFailureNeverDowngradesCompletedSale() {
        UUID saleId = sale("fail");
        runAll(saleId);
        process.fail(saleId, "late failure");
        assertEquals("COMPLETED", saleStatus(saleId));
        UUID other = sale("fail-other");
        process.fail(other, "States.Http.StatusCode.503");
        assertEquals("FAILED", saleStatus(other));
        runAll(other); // operator retry after FAILED still completes
        assertEquals("COMPLETED", saleStatus(other));
    }
}
