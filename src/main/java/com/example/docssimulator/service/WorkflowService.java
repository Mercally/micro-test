package com.example.docssimulator.service;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.PDPageContentStream;
import org.apache.pdfbox.pdmodel.font.PDType1Font;
import org.apache.pdfbox.pdmodel.font.Standard14Fonts;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.example.docssimulator.api.Requests;

@Service
public class WorkflowService {

    private static final Logger log = LoggerFactory.getLogger(WorkflowService.class);

    private final JdbcTemplate jdbcTemplate;
    private final FailureSimulator failureSimulator;
    private final S3Storage s3Storage;

    public WorkflowService(JdbcTemplate jdbcTemplate, FailureSimulator failureSimulator, S3Storage s3Storage) {
        this.jdbcTemplate = jdbcTemplate;
        this.failureSimulator = failureSimulator;
        this.s3Storage = s3Storage;
    }

    @Transactional
    public Map<String, Object> registerSale(Requests.RegisterSaleRequest request) {
        failureSimulator.beforeCall("endpoint0");
        jdbcTemplate.update("""
                    INSERT INTO endpoint0.sales
                        (id, external_reference, customer_name, customer_email, product_code, status)
                    VALUES (?, ?, ?, ?, ?, 'PENDING') ON CONFLICT (external_reference) DO NOTHING
                    """, UUID.randomUUID(), request.externalReference(), request.customerName(),
                    request.customerEmail(), request.productCode());
        UUID saleId = findId("SELECT id FROM endpoint0.sales WHERE external_reference = ? FOR UPDATE", request.externalReference());
        var existing = jdbcTemplate.queryForMap("SELECT * FROM endpoint0.sales WHERE id = ?", saleId);
        if (!request.customerName().equals(existing.get("customer_name"))
                || !request.customerEmail().equals(existing.get("customer_email"))
                || !request.productCode().equals(existing.get("product_code"))) {
            throw new com.example.docssimulator.api.PurchaseException(409, "REFERENCE_CONFLICT", "La referencia ya tiene otro contenido");
        }
        log.info("endpoint0 sale registered saleId={} externalReference={}", saleId, request.externalReference());
        return Map.of("saleId", saleId.toString(), "status", existing.get("status"));
    }

    @Transactional
    public Map<String, Object> updateSaleStatus(UUID saleId, String status) {

        // Validar estados permitidos
        if (!Set.of("PROCESSING", "COMPLETED", "FAILED").contains(status)) {
            throw new IllegalArgumentException("Estado no permitido: " + status);
        }

        // Validar existencia de la venta
        requireSale(saleId);
        String current = jdbcTemplate.queryForObject("SELECT status FROM endpoint0.sales WHERE id = ?", String.class, saleId);
        if ("COMPLETED".equals(current)) {
            return Map.of("saleId", saleId.toString(), "status", current);
        }
        if ("COMPLETED".equals(status)) {
            requireStep("SELECT id FROM endpoint5.document_releases WHERE sale_id = ?", saleId, "Falta liberar el documento");
        }

        // Actualizar estado
        jdbcTemplate.update("""
        UPDATE endpoint0.sales
        SET status = ?
        WHERE id = ?
        """, status, saleId);

        log.info("Sale status updated saleId={} status={}", saleId, status);

        return Map.of(
                "saleId", saleId.toString(),
                "status", status
        );
    }

    @Transactional
    public Map<String, Object> createDocument(Requests.SaleStepRequest request) {
        requireSale(request.saleId());
        UUID documentId = findId("SELECT id FROM endpoint1.documents WHERE sale_id = ?", request.saleId());
        if (documentId == null) {
            failureSimulator.beforeCall("endpoint1");
            documentId = UUID.randomUUID();
            byte[] pdf = createPdf(request.saleId());
            jdbcTemplate.update("""
                    INSERT INTO endpoint1.documents (id, sale_id, file_name, content, status)
                    VALUES (?, ?, ?, ?, 'CREATED')
                    """, documentId, request.saleId(), documentId + ".pdf", pdf);
        }
        log.info("endpoint1 document created saleId={} documentId={}", request.saleId(), documentId);
        return Map.of("saleId", request.saleId().toString(), "documentId", documentId.toString(), "status", "CREATED");
    }

    @Transactional
    public Map<String, Object> createPolicy(Requests.SaleStepRequest request) {
        requireSale(request.saleId());
        UUID policyId = findId("SELECT id FROM endpoint2.policies WHERE sale_id = ?", request.saleId());
        if (policyId == null) {
            failureSimulator.beforeCall("endpoint2");
            policyId = UUID.randomUUID();
            jdbcTemplate.update("""
                    INSERT INTO endpoint2.policies (id, sale_id, policy_number, status)
                    VALUES (?, ?, ?, 'INITIAL')
                    """, policyId, request.saleId(), "POL-" + policyId.toString().toUpperCase());
        }
        log.info("endpoint2 policy created saleId={} policyId={}", request.saleId(), policyId);
        return Map.of("saleId", request.saleId().toString(), "policyId", policyId.toString(), "status", "INITIAL");
    }

    @Transactional
    public Map<String, Object> uploadDocument(Requests.DocumentStepRequest request) {
        requireSale(request.saleId());
        byte[] content = jdbcTemplate.query("""
                        SELECT content FROM endpoint1.documents WHERE id = ? AND sale_id = ?
                        """,
                resultSet -> resultSet.next() ? resultSet.getBytes("content") : null,
                request.documentId(), request.saleId());
        if (content == null) {
            throw new IllegalArgumentException("El documento no existe para esta venta");
        }

        String key = objectKey(request.saleId(), request.documentId());
        UUID uploadId = findId("SELECT id FROM endpoint3.uploads WHERE sale_id = ?", request.saleId());
        if (uploadId == null) {
            failureSimulator.beforeCall("endpoint3");
            s3Storage.upload(key, content);
            uploadId = UUID.randomUUID();
            jdbcTemplate.update("""
                    INSERT INTO endpoint3.uploads (id, sale_id, document_id, bucket, object_key, status)
                    VALUES (?, ?, ?, ?, ?, 'UPLOADED')
                    """, uploadId, request.saleId(), request.documentId(), s3Storage.bucket(), key);
        }
        log.info("endpoint3 document uploaded saleId={} documentId={} objectKey={}",
                request.saleId(), request.documentId(), key);
        return Map.of("saleId", request.saleId().toString(), "documentId", request.documentId().toString(),
                "status", "UPLOADED");
    }

    @Transactional
    public Map<String, Object> completePolicy(Requests.PolicyStepRequest request) {
        requireSale(request.saleId());
        requireStep("SELECT id FROM endpoint1.documents WHERE sale_id = ?", request.saleId(), "Falta crear el documento");
        requireStep("SELECT id FROM endpoint3.uploads WHERE sale_id = ?", request.saleId(), "Falta subir el documento a S3");
        UUID policyId = findId("SELECT id FROM endpoint2.policies WHERE sale_id = ?", request.saleId());
        if (policyId == null || !policyId.equals(request.policyId())) {
            throw new IllegalArgumentException("La póliza no existe para esta venta");
        }

        UUID completionId = findId("SELECT id FROM endpoint4.policy_completions WHERE sale_id = ?", request.saleId());
        if (completionId == null) {
            failureSimulator.beforeCall("endpoint4");
            completionId = UUID.randomUUID();
            jdbcTemplate.update("""
                    INSERT INTO endpoint4.policy_completions (id, sale_id, policy_id, status)
                    VALUES (?, ?, ?, 'COMPLETED')
                    """, completionId, request.saleId(), policyId);
        }
        log.info("endpoint4 policy completed saleId={} policyId={}", request.saleId(), policyId);
        return Map.of("saleId", request.saleId().toString(), "policyId", policyId.toString(), "status", "COMPLETED");
    }

    @Transactional
    public Map<String, Object> releaseDocument(Requests.DocumentStepRequest request) {
        requireSale(request.saleId());
        requireStep("SELECT id FROM endpoint4.policy_completions WHERE sale_id = ?", request.saleId(),
                "La póliza debe estar completada antes de liberar el documento");
        String key = jdbcTemplate.query("""
                        SELECT object_key FROM endpoint3.uploads WHERE sale_id = ? AND document_id = ?
                        """,
                resultSet -> resultSet.next() ? resultSet.getString("object_key") : null,
                request.saleId(), request.documentId());
        if (key == null) {
            throw new IllegalArgumentException("El documento no está cargado en S3");
        }

        String downloadUrl = s3Storage.createDownloadUrl(key);
        if (findId("SELECT id FROM endpoint5.document_releases WHERE sale_id = ?", request.saleId()) == null) {
            failureSimulator.beforeCall("endpoint5");
        }
        jdbcTemplate.update("""
                INSERT INTO endpoint5.document_releases (id, sale_id, document_id, object_key, download_url, status)
                VALUES (?, ?, ?, ?, ?, 'RELEASED')
                ON CONFLICT (sale_id) DO NOTHING
                """, UUID.randomUUID(), request.saleId(), request.documentId(), key, downloadUrl);
        log.info("endpoint5 document released saleId={} documentId={}", request.saleId(), request.documentId());
        return Map.of("saleId", request.saleId().toString(), "documentId", request.documentId().toString(),
                "status", "RELEASED", "downloadUrl", downloadUrl);
    }

    private UUID findId(String sql, Object... parameters) {
        try {
            return jdbcTemplate.queryForObject(sql, (resultSet, rowNum) -> resultSet.getObject("id", UUID.class), parameters);
        } catch (EmptyResultDataAccessException exception) {
            return null;
        }
    }

    private void requireSale(UUID saleId) {
        requireStep("SELECT id FROM endpoint0.sales WHERE id = ? FOR UPDATE", saleId, "La venta no existe");
    }

    private void requireStep(String sql, UUID saleId, String message) {
        if (findId(sql, saleId) == null) {
            throw new IllegalArgumentException(message);
        }
    }

    private String objectKey(UUID saleId, UUID documentId) {
        return "sales/" + saleId + "/" + documentId + ".pdf";
    }

    private byte[] createPdf(UUID saleId) {
        try (PDDocument document = new PDDocument(); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            PDPage page = new PDPage();
            document.addPage(page);
            try (PDPageContentStream content = new PDPageContentStream(document, page)) {
                content.beginText();
                content.setFont(new PDType1Font(Standard14Fonts.FontName.HELVETICA), 14);
                content.newLineAtOffset(72, 720);
                content.showText("Documento de prueba - Seguro " + saleId);
                content.endText();
            }
            document.save(output);
            return output.toByteArray();
        } catch (IOException exception) {
            throw new IllegalStateException("No se pudo generar el PDF de prueba", exception);
        }
    }
}
