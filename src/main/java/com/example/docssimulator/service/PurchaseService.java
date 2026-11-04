package com.example.docssimulator.service;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

import com.example.docssimulator.api.PurchaseException;
import com.example.docssimulator.api.Requests;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class PurchaseService {
    private final JdbcTemplate db;
    private final WorkflowService workflow;
    private final S3Storage storage;
    private final ObjectMapper json;

    public PurchaseService(JdbcTemplate db, WorkflowService workflow, S3Storage storage, ObjectMapper json) {
        this.db = db;
        this.workflow = workflow;
        this.storage = storage;
        this.json = json;
    }

    @Transactional
    public Map<String, Object> accept(Requests.RegisterSaleRequest request, String failure) {
        UUID saleId = UUID.fromString(workflow.registerSale(request).get("saleId").toString());
        var sale = sale(saleId, true);
        if (sale.get("current_event_id") == null && !"COMPLETED".equals(sale.get("status"))) {
            enqueue(saleId, ((Number) sale.get("generation")).intValue() + 1, failure);
        }
        return status(saleId);
    }

    @Transactional
    public Map<String, Object> retry(UUID saleId) {
        var sale = sale(saleId, true);
        if ("COMPLETED".equals(sale.get("status"))) return status(saleId);
        if (!recoverable(sale)) {
            throw new PurchaseException(409, "RUN_ACTIVE", "Solo se recupera una venta FAILED o un lease vencido");
        }
        enqueue(saleId, ((Number) sale.get("generation")).intValue() + 1, null);
        return status(saleId);
    }

    private void enqueue(UUID saleId, int generation, String failure) {
        UUID eventId = UUID.randomUUID();
        Map<String, Object> event = Map.of("eventId", eventId, "type", "PurchaseAccepted", "version", 1,
                "saleId", saleId, "correlationId", saleId, "generation", generation);
        String payload;
        try { payload = json.writeValueAsString(event); }
        catch (JsonProcessingException e) { throw new IllegalStateException(e); }
        db.update("""
                INSERT INTO endpoint0.outbox(event_id,sale_id,generation,event_type,version,payload)
                VALUES (?,?,?,'PurchaseAccepted',1,?::jsonb)
                """, eventId, saleId, generation, payload);
        db.update("""
                UPDATE endpoint0.sales SET current_event_id=?,generation=?,status='ACCEPTED',last_error=NULL,
                  workflow_owner=NULL,workflow_lease_until=now()+interval '5 minutes',updated_at=now() WHERE id=?
                """, eventId, generation, saleId);
        if (failure != null && !failure.isBlank()) {
            String[] parts = failure.split(":");
            if (parts.length != 3 || !java.util.Set.of("document", "policy", "upload", "completePolicy", "release").contains(parts[0])
                    || !java.util.Set.of("transient", "permanent").contains(parts[1])
                    || !parts[2].matches("[1-3]")) {
                throw new PurchaseException(400, "INVALID_LAB_FAULT", "Ejemplo: document:transient:2 o upload:permanent:1");
            }
            db.update("INSERT INTO endpoint0.lab_faults(event_id,step,remaining,permanent) VALUES (?,?,?,?)",
                    eventId, parts[0], Integer.parseInt(parts[2]), "permanent".equals(parts[1]));
        }
    }

    @Transactional(readOnly = true)
    public Map<String, Object> status(UUID saleId) {
        var sale = sale(saleId, false);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("saleId", saleId);
        result.put("correlationId", saleId);
        result.put("status", sale.get("status"));
        result.put("eventId", sale.get("current_event_id"));
        result.put("generation", sale.get("generation"));
        result.put("executionArn", sale.get("workflow_owner"));
        result.put("error", sale.get("last_error"));
        result.put("recoverable", recoverable(sale));
        result.put("updatedAt", sale.get("updated_at"));
        if (recoverable(sale) && sale.get("last_error") == null) {
            result.put("error", "WORKFLOW_LEASE_EXPIRED: consultar ejecución; recuperación explícita disponible");
        }
        result.put("outbox", db.queryForList("""
                SELECT event_id,generation,published_at,sqs_message_id,attempts,last_error
                FROM endpoint0.outbox WHERE sale_id=? ORDER BY generation
                """, saleId));
        var releases = db.queryForList("SELECT document_id,object_key,released_at FROM endpoint5.document_releases WHERE sale_id=?", saleId);
        if (!releases.isEmpty()) {
            result.put("document", releases.get(0));
            result.put("downloadUrl", storage.createDownloadUrl(releases.get(0).get("object_key").toString()));
        }
        return result;
    }

    @Transactional
    public Map<String, Object> claim(UUID saleId, UUID eventId, String owner) {
        var sale = sale(saleId, true);
        boolean same = eventId.equals(sale.get("current_event_id"));
        if (!same || "COMPLETED".equals(sale.get("status")) || "FAILED".equals(sale.get("status"))) {
            return Map.of("claimed", false, "reason", "STALE_OR_TERMINAL");
        }
        if (sale.get("workflow_owner") != null) {
            // A retry by this same execution is safe. Another execution never steals a run.
            return Map.of("claimed", owner.equals(sale.get("workflow_owner")), "reason", "ALREADY_CLAIMED");
        }
        db.update("""
                UPDATE endpoint0.sales SET status='PROCESSING',workflow_owner=?,
                  workflow_lease_until=now()+interval '5 minutes',updated_at=now() WHERE id=?
                """, owner, saleId);
        return Map.of("claimed", true);
    }

    @Transactional
    public Map<String, Object> finish(UUID saleId, UUID eventId, String owner, String error) {
        var sale = sale(saleId, true);
        if ("COMPLETED".equals(sale.get("status"))) return Map.of("status", "COMPLETED");
        checkOwner(sale, eventId, owner);
        if (error == null) workflow.updateSaleStatus(saleId, "COMPLETED");
        db.update("UPDATE endpoint0.sales SET status=?,last_error=?,updated_at=now() WHERE id=?",
                error == null ? "COMPLETED" : "FAILED", error == null ? null : error.substring(0, Math.min(4000, error.length())), saleId);
        return Map.of("status", error == null ? "COMPLETED" : "FAILED");
    }

    public void verifyRun(UUID saleId, UUID eventId, String owner) {
        checkOwner(sale(saleId, false), eventId, owner);
    }

    private void checkOwner(Map<String, Object> sale, UUID eventId, String owner) {
        if (!eventId.equals(sale.get("current_event_id")) || !owner.equals(sale.get("workflow_owner"))
                || !"PROCESSING".equals(sale.get("status"))) {
            throw new PurchaseException(409, "STALE_RUN", "Ejecución obsoleta o terminal");
        }
    }

    private boolean recoverable(Map<String, Object> sale) {
        if ("FAILED".equals(sale.get("status"))) return true;
        Object lease = sale.get("workflow_lease_until");
        return java.util.Set.of("PROCESSING", "ACCEPTED").contains(sale.get("status")) && lease instanceof java.sql.Timestamp timestamp
                && timestamp.toInstant().isBefore(Instant.now());
    }

    private Map<String, Object> sale(UUID id, boolean lock) {
        var rows = db.queryForList("SELECT * FROM endpoint0.sales WHERE id=?" + (lock ? " FOR UPDATE" : ""), id);
        if (rows.isEmpty()) throw new PurchaseException(404, "SALE_NOT_FOUND", "Venta no encontrada");
        return rows.get(0);
    }
}
