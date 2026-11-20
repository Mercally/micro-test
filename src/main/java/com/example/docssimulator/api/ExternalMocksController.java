package com.example.docssimulator.api;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Lab stand-ins for Content Manager and the insurer. Rare random failures of two kinds:
 * before storing (nothing happened) and after storing (acknowledgement lost) - the case
 * the reconciliation queries exist for.
 */
@RestController
@RequestMapping("/mock")
@ConditionalOnProperty(name = "app.mocks.enabled", havingValue = "true")
public class ExternalMocksController {
    private final JdbcTemplate db;
    private final double failureProbability;
    private final long latencyMs;

    public ExternalMocksController(JdbcTemplate db,
                                   @Value("${app.mocks.failure-probability:0.02}") double failureProbability,
                                   @Value("${app.mocks.latency-ms:0}") long latencyMs) {
        if (failureProbability < 0 || failureProbability > 1 || latencyMs < 0) {
            throw new IllegalArgumentException("Configuración inválida de mocks");
        }
        this.db = db;
        this.failureProbability = failureProbability;
        this.latencyMs = latencyMs;
    }

    @GetMapping("/cm/documents/{saleId}")
    public ResponseEntity<Map<String, Object>> findDocument(@PathVariable UUID saleId) {
        return found("documentId", db.queryForList("SELECT document_id FROM mock.cm_documents WHERE sale_id=?", String.class, saleId));
    }

    @PutMapping("/cm/documents/{saleId}")
    public Map<String, Object> storeDocument(@PathVariable UUID saleId, @RequestBody byte[] pdf) {
        maybeFail("cm-unavailable");
        db.update("INSERT INTO mock.cm_documents(sale_id,document_id,size_bytes) VALUES (?,?,?) ON CONFLICT (sale_id) DO NOTHING",
                saleId, "CM-" + saleId, pdf.length);
        maybeFail("cm-ack-lost");
        return Map.of("documentId", "CM-" + saleId);
    }

    @GetMapping("/insurer/policies/{saleId}")
    public ResponseEntity<Map<String, Object>> findPolicy(@PathVariable UUID saleId) {
        return found("policyNumber", db.queryForList("SELECT policy_number FROM mock.insurer_policies WHERE sale_id=?", String.class, saleId));
    }

    @PostMapping("/insurer/policies/{saleId}")
    public Map<String, Object> issuePolicy(@PathVariable UUID saleId) {
        maybeFail("insurer-unavailable");
        db.update("INSERT INTO mock.insurer_policies(sale_id,policy_number) VALUES (?,?) ON CONFLICT (sale_id) DO NOTHING",
                saleId, "POL-" + saleId);
        maybeFail("insurer-ack-lost");
        return Map.of("policyNumber", "POL-" + saleId);
    }

    private ResponseEntity<Map<String, Object>> found(String field, List<String> rows) {
        sleep();
        return rows.isEmpty() ? ResponseEntity.notFound().build() : ResponseEntity.ok(Map.of(field, rows.get(0)));
    }

    private void maybeFail(String point) {
        sleep();
        if (ThreadLocalRandom.current().nextDouble() < failureProbability) throw new TransientWorkflowException("mock " + point);
    }

    private void sleep() {
        if (latencyMs == 0) return;
        try {
            Thread.sleep(latencyMs);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
        }
    }
}
