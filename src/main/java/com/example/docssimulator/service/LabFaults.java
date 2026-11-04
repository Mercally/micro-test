package com.example.docssimulator.service;

import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class LabFaults {
    private final JdbcTemplate db;
    public LabFaults(JdbcTemplate db) { this.db = db; }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public String consume(UUID eventId, String step) {
        var rows = db.queryForList("SELECT * FROM endpoint0.lab_faults WHERE event_id=? AND step=? FOR UPDATE", eventId, step);
        if (rows.isEmpty()) return "";
        var fault = rows.get(0);
        if (Boolean.TRUE.equals(fault.get("permanent"))) return "PERMANENT";
        if (((Number) fault.get("remaining")).intValue() <= 0) return "";
        db.update("UPDATE endpoint0.lab_faults SET remaining=remaining-1 WHERE event_id=?", eventId);
        return "TRANSIENT";
    }
}
