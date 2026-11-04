package com.example.docssimulator.service;

import com.example.docssimulator.api.Requests;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import software.amazon.awssdk.services.s3.S3Client;
import static org.junit.jupiter.api.Assertions.*;

/** Component integration: Java + isolated PostgreSQL + laptop S3. No API/Pipe/SFN claim. */
@SpringBootTest(properties = {"app.simulator.failure-probability=0", "app.lab.purchases-enabled=true",
        "app.s3.endpoint=${LAB_TEST_S3_ENDPOINT}", "app.s3.presign-endpoint=${LAB_TEST_S3_ENDPOINT}",
        "app.s3.bucket=micro-test-local-documents", "logging.level.root=WARN", "debug=false"})
@EnabledIfEnvironmentVariable(named = "LAB_TEST_S3_ENDPOINT", matches = "http://(100\\.100\\.154\\.68|127\\.0\\.0\\.1):4566")
class PurchaseS3IntegrationTest {
    @Autowired PurchaseService purchases;
    @Autowired WorkflowService workflow;
    @Autowired JdbcTemplate db;
    @Autowired S3Client s3;

    @Test
    void realPdfUploadAndPresignedDownloadReuseOriginalObject() throws Exception {
        assertEquals("docs_business_test", db.queryForObject("SELECT current_database()", String.class));
        var sale = purchases.accept(new Requests.RegisterSaleRequest("s3-component-" + UUID.randomUUID(),
                "Local component proof", "practice@example.com", "AUTO-01"), null);
        UUID id = (UUID) sale.get("saleId"), eventId = (UUID) sale.get("eventId");
        String owner = "component-test-" + UUID.randomUUID();
        purchases.claim(id, eventId, owner);
        UUID document = UUID.fromString(workflow.createDocument(new Requests.SaleStepRequest(id)).get("documentId").toString());
        UUID policy = UUID.fromString(workflow.createPolicy(new Requests.SaleStepRequest(id)).get("policyId").toString());
        var request = new Requests.DocumentStepRequest(id, document);
        workflow.uploadDocument(request);
        String key = "sales/" + id + "/" + document + ".pdf";
        var original = s3.getObjectAsBytes(b -> b.bucket("micro-test-local-documents").key(key));
        byte[] bytes = original.asByteArray();
        assertTrue(new String(bytes, 0, 5, java.nio.charset.StandardCharsets.US_ASCII).equals("%PDF-"));
        assertEquals("application/pdf", original.response().contentType());
        workflow.uploadDocument(request);
        assertEquals(original.response().versionId(), s3.headObject(b -> b.bucket("micro-test-local-documents").key(key)).versionId());
        workflow.completePolicy(new Requests.PolicyStepRequest(id, policy));
        workflow.releaseDocument(request);
        purchases.finish(id, eventId, owner, null);
        var status = purchases.status(id);
        assertEquals("COMPLETED", status.get("status"));
        URI signed = URI.create(status.get("downloadUrl").toString());
        assertEquals(URI.create(System.getenv("LAB_TEST_S3_ENDPOINT")).getAuthority(), signed.getAuthority());
        HttpURLConnection connection = (HttpURLConnection) signed.toURL().openConnection(Proxy.NO_PROXY);
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(5000);
        connection.setReadTimeout(10000);
        try {
            assertEquals(200, connection.getResponseCode());
            try (var stream = connection.getInputStream()) { assertArrayEquals(bytes, stream.readAllBytes()); }
        } finally { connection.disconnect(); }
        var evidence = Map.of("status", "PASS", "scope", "Java + PostgreSQL Mac + S3 laptop; no API/Pipe/SFN",
                "saleId", id.toString(), "eventId", eventId.toString(), "objectKey", key,
                "versionId", original.response().versionId(), "sha256", HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)),
                "endpoint", System.getenv("LAB_TEST_S3_ENDPOINT"));
        Path report = Path.of("localstack/.state/business/s3-component.json");
        Files.createDirectories(report.getParent());
        Files.writeString(report, new com.fasterxml.jackson.databind.ObjectMapper().writerWithDefaultPrettyPrinter().writeValueAsString(evidence));
    }
}
