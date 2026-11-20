package com.example.docssimulator.api;

import java.util.Map;
import java.util.Set;
import java.util.UUID;

import com.example.docssimulator.service.ProcessHistory;
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
    private static final Set<String> STEPS = Set.of("document", "cm", "insurer", "cleanup", "fail");
    private static final Set<String> PACED = Set.of("document", "cm", "insurer");
    private final ProcessService process;
    private final ProcessHistory history;

    public ProcessController(ProcessService process, ProcessHistory history) {
        this.process = process;
        this.history = history;
    }

    /** eventId only selects lab faults; idempotency is per sale and step. */
    public record StepRequest(UUID eventId, String error) {}

    /** Journals every attempt (RUNNING → outcome) and runs the lab's simulated work before business steps. */
    @PostMapping("/{step}")
    public Map<String, Object> run(@PathVariable UUID saleId, @PathVariable String step,
                                   @RequestBody(required = false) StepRequest body) throws InterruptedException {
        if (!STEPS.contains(step)) throw new PurchaseException(404, "UNKNOWN_STEP", step);
        long started = System.nanoTime();
        ProcessHistory.Work work = PACED.contains(step) ? history.work() : ProcessHistory.Work.NONE;
        long id = history.start(saleId, step, work);
        try {
            ProcessHistory.simulate(work);
            Map<String, Object> result = execute(saleId, step, body);
            history.finish(id, String.valueOf(result.getOrDefault("outcome", result.get("status"))), null, elapsedMs(started));
            return result;
        } catch (PurchaseException e) {
            history.finish(id, "ERROR", e.code() + ": " + e.getMessage(), elapsedMs(started));
            throw e;
        } catch (RuntimeException | InterruptedException e) {
            history.finish(id, "ERROR", e.getMessage(), elapsedMs(started));
            throw e;
        }
    }

    private Map<String, Object> execute(UUID saleId, String step, StepRequest body) {
        UUID eventId = body == null ? null : body.eventId();
        return switch (step) {
            case "document" -> process.document(saleId, eventId);
            case "cm" -> process.cm(saleId, eventId);
            case "insurer" -> process.insurer(saleId, eventId);
            case "cleanup" -> process.cleanup(saleId);
            default -> process.fail(saleId, body == null ? null : body.error());
        };
    }

    private static long elapsedMs(long started) { return (System.nanoTime() - started) / 1_000_000; }
}
