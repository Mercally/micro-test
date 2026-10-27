package com.example.docssimulator.api;

import java.util.Map;

import com.example.docssimulator.service.WorkflowService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1")
public class WorkflowController {

    private final WorkflowService workflowService;

    public WorkflowController(WorkflowService workflowService) {
        this.workflowService = workflowService;
    }

    @PostMapping("/endpoint0/sales")
    ResponseEntity<Map<String, Object>> registerSale(@Valid @RequestBody Requests.RegisterSaleRequest request) {
        return ResponseEntity.ok(workflowService.registerSale(request));
    }

    @PostMapping("/endpoint1/documents")
    ResponseEntity<Map<String, Object>> createDocument(@Valid @RequestBody Requests.SaleStepRequest request) {
        return ResponseEntity.ok(workflowService.createDocument(request));
    }

    @PostMapping("/endpoint2/policies")
    ResponseEntity<Map<String, Object>> createPolicy(@Valid @RequestBody Requests.SaleStepRequest request) {
        return ResponseEntity.ok(workflowService.createPolicy(request));
    }

    @PostMapping("/endpoint3/documents/upload")
    ResponseEntity<Map<String, Object>> uploadDocument(@Valid @RequestBody Requests.DocumentStepRequest request) {
        return ResponseEntity.ok(workflowService.uploadDocument(request));
    }

    @PostMapping("/endpoint4/policies/complete")
    ResponseEntity<Map<String, Object>> completePolicy(@Valid @RequestBody Requests.PolicyStepRequest request) {
        return ResponseEntity.ok(workflowService.completePolicy(request));
    }

    @PostMapping("/endpoint5/documents/release")
    ResponseEntity<Map<String, Object>> releaseDocument(@Valid @RequestBody Requests.DocumentStepRequest request) {
        return ResponseEntity.ok(workflowService.releaseDocument(request));
    }
}