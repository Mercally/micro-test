package com.example.docssimulator.service;

import java.util.Map;
import java.util.UUID;

import com.example.docssimulator.api.PurchaseException;
import com.example.docssimulator.api.TransientWorkflowException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * AWS asynchronous flow (N02). Every step may be invoked any number of times, by any execution:
 * check recorded state first, reconcile with the external system, act only if really missing,
 * then record. No owner/lease: a redelivered message finds finished work and moves on.
 */
@Service
public class ProcessService {
    static final String DOCUMENT = "DOCUMENT";
    static final String CM = "CM_UPLOAD";
    static final String INSURER = "INSURER";
    private static final Logger log = LoggerFactory.getLogger(ProcessService.class);

    private final JdbcTemplate db;
    private final S3Storage storage;
    private final ExternalSystems external;
    private final LabFaults faults;
    private final WorkflowService pdf;

    public ProcessService(JdbcTemplate db, S3Storage storage, ExternalSystems external, LabFaults faults, WorkflowService pdf) {
        this.db = db;
        this.storage = storage;
        this.external = external;
        this.faults = faults;
        this.pdf = pdf;
    }

    // ponytail: the sale row lock is held across S3/HTTP calls so duplicate executions serialize per sale.
    // Ceiling: one pooled connection per in-flight step. Upgrade path: short "in progress" marker + no lock during I/O.

    /** Step 1: prepare, fill and sign the document (real SHA256withRSA over its content); store it in temporary S3. */
    @Transactional
    public Map<String, Object> document(UUID saleId, UUID eventId) { return document(saleId, eventId, ProcessHistory.Work.none()); }

    @Transactional
    public Map<String, Object> cm(UUID saleId, UUID eventId) { return cm(saleId, eventId, ProcessHistory.Work.none()); }

    @Transactional
    public Map<String, Object> insurer(UUID saleId, UUID eventId) { return insurer(saleId, eventId, ProcessHistory.Work.none()); }

    /** Lab work runs only on the path that really acts, inside the step like the real work it stands for. */
    private static void act(ProcessHistory.Work work) {
        try {
            ProcessHistory.simulate(work);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Trabajo simulado interrumpido", e);
        }
    }

    @Transactional
    public Map<String, Object> document(UUID saleId, UUID eventId, ProcessHistory.Work work) {
        lockSale(saleId);
        String key = key(saleId);
        // Done means "file available for step 2" or step 2 already consumed it.
        if (done(saleId, DOCUMENT) && (done(saleId, CM) || storage.exists(key))) return result(saleId, DOCUMENT, "ALREADY_DONE");
        fault(eventId, "document");
        act(work);
        storage.upload(key, pdf.createPdf(saleId, documentBytes()));
        if (!storage.exists(key)) throw new TransientWorkflowException("document: archivo no verificado en S3");
        record(saleId, DOCUMENT, key, "DONE");
        db.update("UPDATE endpoint0.sales SET status='PROCESSING',last_error=NULL,updated_at=now() WHERE id=? AND status<>'COMPLETED'", saleId);
        return result(saleId, DOCUMENT, "DONE");
    }

    /** Step 2: upload the document to Content Manager. */
    @Transactional
    public Map<String, Object> cm(UUID saleId, UUID eventId, ProcessHistory.Work work) {
        lockSale(saleId);
        if (done(saleId, CM)) return result(saleId, CM, "ALREADY_DONE");
        require(saleId, DOCUMENT);
        fault(eventId, "cm");
        String documentId = external.findCmDocument(saleId);
        String outcome = "RECONCILED";
        if (documentId == null) {
            String key = key(saleId);
            // Recorded as done but file gone: step 1 must regenerate it on the next delivery.
            if (!storage.exists(key)) throw new PurchaseException(409, "DOCUMENT_MISSING", "Archivo temporal ausente; reintento global");
            act(work);
            external.storeCmDocument(saleId, storage.download(key));
            documentId = external.findCmDocument(saleId);
            if (documentId == null) throw new TransientWorkflowException("cm: documento no verificado");
            outcome = "DONE";
        }
        record(saleId, CM, documentId, outcome);
        return result(saleId, CM, outcome);
    }

    /** Step 3: issue the policy with the insurer and require its acknowledgement. */
    @Transactional
    public Map<String, Object> insurer(UUID saleId, UUID eventId, ProcessHistory.Work work) {
        lockSale(saleId);
        if (done(saleId, INSURER)) return result(saleId, INSURER, "ALREADY_DONE");
        require(saleId, CM);
        fault(eventId, "insurer");
        String policyNumber = external.findInsurerPolicy(saleId);
        String outcome = "RECONCILED";
        if (policyNumber == null) {
            act(work);
            policyNumber = external.sendInsurerPolicy(saleId);
            outcome = "DONE";
        }
        record(saleId, INSURER, policyNumber, outcome);
        db.update("UPDATE endpoint0.sales SET status='COMPLETED',last_error=NULL,updated_at=now() WHERE id=?", saleId);
        return result(saleId, INSURER, outcome);
    }

    /** After the SQS message is deleted: remove the temporary file. Never before completion. */
    public Map<String, Object> cleanup(UUID saleId) {
        if (!"COMPLETED".equals(status(saleId))) {
            throw new PurchaseException(409, "NOT_COMPLETED", "El archivo temporal solo se borra con la venta completada");
        }
        storage.delete(key(saleId));
        return Map.of("saleId", saleId.toString(), "step", "CLEANUP", "outcome", "DONE");
    }

    /** Last delivery exhausted: visible FAILED state for operators. Never downgrades a completed sale. */
    @Transactional
    public Map<String, Object> fail(UUID saleId, String error) {
        lockSale(saleId);
        String message = error == null || error.isBlank() ? "WORKFLOW_FAILED" : error.substring(0, Math.min(4000, error.length()));
        db.update("UPDATE endpoint0.sales SET status='FAILED',last_error=?,updated_at=now() WHERE id=? AND status<>'COMPLETED'",
                message, saleId);
        return Map.of("saleId", saleId.toString(), "status", status(saleId));
    }

    /** Random size in [document_min_kb, document_max_kb] from process.lab_settings (0 when absent). */
    private int documentBytes() {
        var rows = db.queryForList("SELECT document_min_kb AS min, document_max_kb AS max FROM process.lab_settings");
        if (rows.isEmpty()) return 0;
        int min = ((Number) rows.get(0).get("min")).intValue();
        int max = Math.max(min, ((Number) rows.get(0).get("max")).intValue());
        return java.util.concurrent.ThreadLocalRandom.current().nextInt(min, max + 1) * 1024;
    }

    private void lockSale(UUID saleId) {
        if (db.queryForList("SELECT id FROM endpoint0.sales WHERE id=? FOR UPDATE", saleId).isEmpty()) {
            throw new PurchaseException(404, "SALE_NOT_FOUND", "Venta no encontrada");
        }
    }

    private String status(UUID saleId) {
        var rows = db.queryForList("SELECT status FROM endpoint0.sales WHERE id=?", String.class, saleId);
        if (rows.isEmpty()) throw new PurchaseException(404, "SALE_NOT_FOUND", "Venta no encontrada");
        return rows.get(0);
    }

    private boolean done(UUID saleId, String step) {
        return !db.queryForList("SELECT 1 FROM process.steps WHERE sale_id=? AND step=?", saleId, step).isEmpty();
    }

    private void require(UUID saleId, String step) {
        if (!done(saleId, step)) throw new PurchaseException(409, "STEP_OUT_OF_ORDER", "Falta paso previo: " + step);
    }

    private void record(UUID saleId, String step, String reference, String outcome) {
        db.update("""
                INSERT INTO process.steps(sale_id,step,reference,outcome) VALUES (?,?,?,?)
                ON CONFLICT (sale_id,step) DO UPDATE SET reference=EXCLUDED.reference,outcome=EXCLUDED.outcome,completed_at=now()
                """, saleId, step, reference, outcome);
        log.info("process_step saleId={} step={} outcome={}", saleId, step, outcome);
    }

    private void fault(UUID eventId, String step) {
        if (eventId == null) return;
        String failure = faults.consume(eventId, step);
        if ("TRANSIENT".equals(failure)) throw new TransientWorkflowException(step);
        if ("PERMANENT".equals(failure)) throw new PurchaseException(422, "LAB_PERMANENT_FAILURE", step);
    }

    private Map<String, Object> result(UUID saleId, String step, String outcome) {
        return Map.of("saleId", saleId.toString(), "step", step, "outcome", outcome);
    }

    static String key(UUID saleId) {
        return "tmp/sales/" + saleId + "/document.pdf";
    }
}
