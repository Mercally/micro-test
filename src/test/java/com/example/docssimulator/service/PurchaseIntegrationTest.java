package com.example.docssimulator.service;

import com.example.docssimulator.api.PurchaseException;
import com.example.docssimulator.api.Requests;
import java.util.ArrayList;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** Real PostgreSQL transactions/locks/PDF; S3 is mocked here, never counted as E2E. */
@SpringBootTest(properties = {"app.simulator.failure-probability=0", "app.lab.purchases-enabled=true",
        "app.s3.endpoint=http://127.0.0.1:4566", "logging.level.root=WARN", "debug=false"})
@EnabledIfEnvironmentVariable(named = "LAB_TEST_POSTGRES", matches = "true")
@AutoConfigureMockMvc
class PurchaseIntegrationTest {
    @Autowired PurchaseService purchases;
    @Autowired WorkflowService workflow;
    @Autowired LabFaults faults;
    @Autowired JdbcTemplate db;
    @Autowired MockMvc http;
    @MockitoBean S3Storage storage;

    @BeforeEach
    void clean() {
        assertEquals("docs_business_test", db.queryForObject("SELECT current_database()", String.class),
                "Tests must never truncate a lab/user database");
        db.execute("TRUNCATE endpoint0.lab_faults,endpoint0.outbox,endpoint0.sales,endpoint1.documents,endpoint2.policies,endpoint3.uploads,endpoint4.policy_completions,endpoint5.document_releases CASCADE");
        when(storage.bucket()).thenReturn("test-bucket");
        when(storage.createDownloadUrl(anyString())).thenReturn("http://127.0.0.1:4566/test.pdf");
    }

    Requests.RegisterSaleRequest request(String reference) {
        return new Requests.RegisterSaleRequest(reference, "Local", "local@example.com", "AUTO-01");
    }

    UUID id(Map<String, Object> sale) { return UUID.fromString(sale.get("saleId").toString()); }

    @Test
    void concurrentAcceptanceHasOneSaleAndOneOutboxEventAndRejectsConflict() throws Exception {
        var executor = Executors.newFixedThreadPool(8);
        try {
            var calls = new ArrayList<Callable<Map<String, Object>>>();
            for (int i = 0; i < 16; i++) calls.add(() -> purchases.accept(request("concurrent"), null));
            var results = executor.invokeAll(calls);
            UUID saleId = id(results.get(0).get());
            for (var result : results) assertEquals(saleId, id(result.get()));
            assertEquals(1, db.queryForObject("SELECT count(*) FROM endpoint0.sales", Integer.class));
            assertEquals(1, db.queryForObject("SELECT count(*) FROM endpoint0.outbox", Integer.class));
            var error = assertThrows(PurchaseException.class, () -> purchases.accept(
                    new Requests.RegisterSaleRequest("concurrent", "Changed", "local@example.com", "AUTO-01"), null));
            assertEquals(409, error.status());
        } finally { executor.shutdownNow(); }
    }

    @Test
    void failedOutboxInsertRollsBackSale() {
        db.execute("ALTER TABLE endpoint0.outbox ADD CONSTRAINT reject_test_event CHECK (event_type <> 'PurchaseAccepted') NOT VALID");
        try {
            assertThrows(RuntimeException.class, () -> purchases.accept(request("rollback"), null));
            assertEquals(0, db.queryForObject("SELECT count(*) FROM endpoint0.sales", Integer.class));
            assertEquals(0, db.queryForObject("SELECT count(*) FROM endpoint0.outbox", Integer.class));
        } finally { db.execute("ALTER TABLE endpoint0.outbox DROP CONSTRAINT reject_test_event"); }
    }

    @Test
    void invalidFaultConfigurationRollsBackBothWrites() {
        assertThrows(PurchaseException.class, () -> purchases.accept(request("bad-fault"), "bad"));
        assertEquals(0, db.queryForObject("SELECT count(*) FROM endpoint0.sales", Integer.class));
        assertEquals(0, db.queryForObject("SELECT count(*) FROM endpoint0.outbox", Integer.class));
    }

    @Test
    void concurrentStepsReusePdfAndIdsAndDoNotRepeatS3Put() throws Exception {
        var accepted = purchases.accept(request("steps"), null);
        UUID sale = id(accepted);
        UUID event = (UUID) accepted.get("eventId");
        assertEquals(true, purchases.claim(sale, event, "execution-1").get("claimed"));
        assertEquals(false, purchases.claim(sale, event, "execution-2").get("claimed"));
        var executor = Executors.newFixedThreadPool(6);
        try {
            var calls = new ArrayList<Callable<Map<String, Object>>>();
            for (int i = 0; i < 12; i++) calls.add(() -> workflow.createDocument(new Requests.SaleStepRequest(sale)));
            var results = executor.invokeAll(calls);
            UUID document = UUID.fromString(results.get(0).get().get("documentId").toString());
            for (var result : results) assertEquals(document.toString(), result.get().get("documentId"));
            byte[] pdf = db.queryForObject("SELECT content FROM endpoint1.documents WHERE sale_id=?", byte[].class, sale);
            assertNotNull(pdf);
            assertTrue(new String(pdf, 0, 5, java.nio.charset.StandardCharsets.US_ASCII).equals("%PDF-"));
            UUID policy = UUID.fromString(workflow.createPolicy(new Requests.SaleStepRequest(sale)).get("policyId").toString());
            assertThrows(IllegalArgumentException.class, () -> workflow.completePolicy(new Requests.PolicyStepRequest(sale, policy)));
            for (int i = 0; i < 3; i++) workflow.uploadDocument(new Requests.DocumentStepRequest(sale, document));
            verify(storage, times(1)).upload(eq("sales/" + sale + "/" + document + ".pdf"), any());
            workflow.completePolicy(new Requests.PolicyStepRequest(sale, policy));
            workflow.releaseDocument(new Requests.DocumentStepRequest(sale, document));
            Object released = db.queryForObject("SELECT released_at FROM endpoint5.document_releases WHERE sale_id=?", java.sql.Timestamp.class, sale);
            workflow.releaseDocument(new Requests.DocumentStepRequest(sale, document));
            assertEquals(released, db.queryForObject("SELECT released_at FROM endpoint5.document_releases WHERE sale_id=?", java.sql.Timestamp.class, sale));
            purchases.finish(sale, event, "execution-1", null);
            workflow.updateSaleStatus(sale, "FAILED");
            assertEquals("COMPLETED", purchases.status(sale).get("status"));
            assertEquals(false, purchases.claim(sale, event, "execution-3").get("claimed"));
        } finally { executor.shutdownNow(); }
    }

    @Test
    void failureRecoveryUsesNewEventRejectsStaleOwnerAndPreservesFaultCounter() {
        var accepted = purchases.accept(request("recover"), "document:transient:2");
        UUID sale = id(accepted), event = (UUID) accepted.get("eventId");
        purchases.claim(sale, event, "old");
        assertThrows(PurchaseException.class, () -> purchases.retry(sale));
        assertEquals("TRANSIENT", faults.consume(event, "document"));
        assertEquals("TRANSIENT", faults.consume(event, "document"));
        assertEquals("", faults.consume(event, "document"));
        purchases.finish(sale, event, "old", "controlled failure");
        assertEquals(true, purchases.status(sale).get("recoverable"));
        var retry = purchases.retry(sale);
        assertNotEquals(event, retry.get("eventId"));
        assertEquals(2, ((Number) retry.get("generation")).intValue());
        assertThrows(PurchaseException.class, () -> purchases.finish(sale, event, "old", "late error"));
        assertEquals("ACCEPTED", purchases.status(sale).get("status"));
    }

    @Test
    void abandonedAcceptanceAndExecutionBecomeExplicitlyRecoverable() {
        var sale = purchases.accept(request("abandoned"), null);
        db.update("UPDATE endpoint0.sales SET workflow_lease_until=now()-interval '1 second' WHERE id=?", id(sale));
        assertEquals(true, purchases.status(id(sale)).get("recoverable"));
        var next = purchases.retry(id(sale));
        purchases.claim(id(sale), (UUID) next.get("eventId"), "timed-out");
        db.update("UPDATE endpoint0.sales SET workflow_lease_until=now()-interval '1 second' WHERE id=?", id(sale));
        assertEquals(true, purchases.status(id(sale)).get("recoverable"));
        assertEquals(3, ((Number) purchases.retry(id(sale)).get("generation")).intValue());
    }

    @Test
    void missingPurchaseIs404AndPrematureCompletionCannotMutateStatus() {
        assertEquals(404, assertThrows(PurchaseException.class, () -> purchases.status(UUID.randomUUID())).status());
        var sale = purchases.accept(request("ordering"), null);
        assertThrows(IllegalArgumentException.class, () -> workflow.updateSaleStatus(id(sale), "COMPLETED"));
        assertEquals("ACCEPTED", purchases.status(id(sale)).get("status"));
    }

    @Test
    void httpContractReturns202AndObservableStatus() throws Exception {
        String body = "{\"externalReference\":\"http-contract\",\"customerName\":\"Local\",\"customerEmail\":\"local@example.com\",\"productCode\":\"AUTO-01\"}";
        var result = http.perform(post("/api/v1/purchases").contentType("application/json").content(body))
                .andExpect(status().isAccepted()).andExpect(jsonPath("$.status").value("ACCEPTED"))
                .andExpect(jsonPath("$.outbox.length()").value(1)).andReturn();
        String id = new com.fasterxml.jackson.databind.ObjectMapper().readTree(result.getResponse().getContentAsString()).get("saleId").asText();
        http.perform(get("/api/v1/purchases/" + id)).andExpect(status().isOk()).andExpect(jsonPath("$.correlationId").value(id));
        http.perform(get("/api/v1/purchases/" + UUID.randomUUID())).andExpect(status().isNotFound());
        http.perform(post("/api/v1/purchases").contentType("application/json").content("{}"))
                .andExpect(status().isBadRequest());
        http.perform(post("/api/v1/purchases/" + id + "/retry")).andExpect(status().isConflict());
    }

    @Test
    void crashBetweenS3PutAndSqlCommitRetriesSamePdfKeyAndBytes() {
        UUID sale = id(purchases.accept(request("s3-boundary"), null));
        UUID document = UUID.fromString(workflow.createDocument(new Requests.SaleStepRequest(sale)).get("documentId").toString());
        var request = new Requests.DocumentStepRequest(sale, document);
        db.execute("ALTER TABLE endpoint3.uploads ADD CONSTRAINT reject_upload_test CHECK (status <> 'UPLOADED') NOT VALID");
        try {
            assertThrows(RuntimeException.class, () -> workflow.uploadDocument(request));
            assertEquals(0, db.queryForObject("SELECT count(*) FROM endpoint3.uploads WHERE sale_id=?", Integer.class, sale));
        } finally { db.execute("ALTER TABLE endpoint3.uploads DROP CONSTRAINT reject_upload_test"); }
        workflow.uploadDocument(request);
        var bytes = org.mockito.ArgumentCaptor.forClass(byte[].class);
        verify(storage, times(2)).upload(eq("sales/" + sale + "/" + document + ".pdf"), bytes.capture());
        assertArrayEquals(bytes.getAllValues().get(0), bytes.getAllValues().get(1));
        assertEquals(1, db.queryForObject("SELECT count(*) FROM endpoint3.uploads WHERE sale_id=?", Integer.class, sale));
    }
}
