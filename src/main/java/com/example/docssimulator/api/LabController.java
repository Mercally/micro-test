package com.example.docssimulator.api;

import java.util.Map;

import com.example.docssimulator.service.LabService;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/** Test panel API (public, like the rest of the lab API: N09). Burst size is capped. */
@RestController
@RequestMapping("/api/v1/lab")
@ConditionalOnProperty(name = "app.lab.panel.enabled", havingValue = "true")
public class LabController {
    private final LabService lab;

    public LabController(LabService lab) { this.lab = lab; }

    public record Settings(int stepDelayMinMs, int stepDelayMaxMs) {}
    public record Burst(int count, String failure) {}

    @GetMapping("/settings")
    public Map<String, Object> settings() { return lab.settings(); }

    @PutMapping("/settings")
    public Map<String, Object> update(@RequestBody Settings s) { return lab.updateSettings(s.stepDelayMinMs(), s.stepDelayMaxMs()); }

    @GetMapping("/system")
    public Map<String, Object> system() { return lab.system(); }

    @PostMapping("/burst")
    public ResponseEntity<Map<String, Object>> burst(@RequestBody Burst b) {
        return ResponseEntity.accepted().body(lab.burst(b.count(), b.failure()));
    }

    @PostMapping("/dlq/redrive")
    public ResponseEntity<Map<String, Object>> redrive() { return ResponseEntity.accepted().body(lab.redrive()); }
}
