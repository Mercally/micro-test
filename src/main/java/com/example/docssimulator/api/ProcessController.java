package com.example.docssimulator.api;

import java.util.Map;
import java.util.UUID;

import com.example.docssimulator.service.ProcessService;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Called only by Step Functions through NLB → Istio. Never routed by API Gateway. */
@RestController
@RequestMapping("/api/v1/internal/purchases/{saleId}/steps")
@ConditionalOnProperty(name = "app.lab.purchases-enabled", havingValue = "true")
public class ProcessController {
    private final ProcessService process;

    public ProcessController(ProcessService process) { this.process = process; }

    /** eventId only selects lab faults; idempotency is per sale and step. */
    public record StepRequest(UUID eventId, String error) {}

    @PostMapping("/{step}")
    public Map<String, Object> run(@PathVariable UUID saleId, @PathVariable String step,
                                   @RequestBody(required = false) StepRequest body) {
        UUID eventId = body == null ? null : body.eventId();
        return switch (step) {
            case "document" -> process.document(saleId, eventId);
            case "cm" -> process.cm(saleId, eventId);
            case "insurer" -> process.insurer(saleId, eventId);
            case "cleanup" -> process.cleanup(saleId);
            case "fail" -> process.fail(saleId, body == null ? null : body.error());
            default -> throw new PurchaseException(404, "UNKNOWN_STEP", step);
        };
    }
}
