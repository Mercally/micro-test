package com.example.docssimulator.api;

import java.util.Map;
import java.util.UUID;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import com.example.docssimulator.service.LabFaults;
import com.example.docssimulator.service.PurchaseService;
import com.example.docssimulator.service.WorkflowService;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1")
@ConditionalOnProperty(name = "app.lab.purchases-enabled", havingValue = "true")
public class PurchaseController {
    private final PurchaseService purchases;
    private final WorkflowService workflow;
    private final LabFaults faults;

    public PurchaseController(PurchaseService purchases, WorkflowService workflow, LabFaults faults) {
        this.purchases = purchases; this.workflow = workflow; this.faults = faults;
    }

    @PostMapping("/purchases")
    public ResponseEntity<Map<String, Object>> accept(@Valid @RequestBody Requests.RegisterSaleRequest request,
            @RequestHeader(value = "X-Lab-Failure", required = false) String failure) {
        return ResponseEntity.accepted().body(purchases.accept(request, failure));
    }

    @GetMapping("/purchases/{saleId}")
    public Map<String, Object> status(@PathVariable UUID saleId) { return purchases.status(saleId); }

    @PostMapping("/purchases/{saleId}/retry")
    public ResponseEntity<Map<String, Object>> retry(@PathVariable UUID saleId) {
        return ResponseEntity.accepted().body(purchases.retry(saleId));
    }

    public record RunRequest(@NotNull UUID eventId, @NotBlank String owner, @NotBlank String operation,
            UUID documentId, UUID policyId, String error) {}

    // Workflow traffic enters through the Istio gateway; API Gateway exposes no internal routes.
    @PostMapping("/internal/purchases/{saleId}/run")
    public Map<String, Object> run(@PathVariable UUID saleId, @Valid @RequestBody RunRequest run) {
        if ("claim".equals(run.operation())) return purchases.claim(saleId, run.eventId(), run.owner());
        if ("finish".equals(run.operation())) return purchases.finish(saleId, run.eventId(), run.owner(), null);
        if ("fail".equals(run.operation())) return purchases.finish(saleId, run.eventId(), run.owner(),
                run.error() == null ? "WORKFLOW_FAILED" : run.error());
        purchases.verifyRun(saleId, run.eventId(), run.owner());
        String failure = faults.consume(run.eventId(), run.operation());
        if ("TRANSIENT".equals(failure)) throw new TransientWorkflowException(run.operation());
        if ("PERMANENT".equals(failure)) throw new PurchaseException(422, "LAB_PERMANENT_FAILURE", run.operation());
        return switch (run.operation()) {
            case "document" -> workflow.createDocument(new Requests.SaleStepRequest(saleId));
            case "policy" -> workflow.createPolicy(new Requests.SaleStepRequest(saleId));
            case "upload" -> workflow.uploadDocument(new Requests.DocumentStepRequest(saleId, run.documentId()));
            case "completePolicy" -> workflow.completePolicy(new Requests.PolicyStepRequest(saleId, run.policyId()));
            case "release" -> workflow.releaseDocument(new Requests.DocumentStepRequest(saleId, run.documentId()));
            default -> throw new PurchaseException(400, "UNKNOWN_OPERATION", run.operation());
        };
    }
}
