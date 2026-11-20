package com.example.docssimulator.service;

import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/**
 * Step attempt journal and lab pacing for the AWS flow. Used outside step transactions:
 * rows survive failed attempts, and the pause never holds the sale lock.
 */
@Service
public class ProcessHistory {
    private static final Logger log = LoggerFactory.getLogger(ProcessHistory.class);
    private final JdbcTemplate db;

    public ProcessHistory(JdbcTemplate db) { this.db = db; }

    /** Random delay in [min,max] from process.lab_settings, read live so SQL changes apply at once. */
    public int delayMs() {
        var rows = db.queryForList("SELECT step_delay_min_ms AS min, step_delay_max_ms AS max FROM process.lab_settings");
        if (rows.isEmpty()) return 0;
        int min = ((Number) rows.get(0).get("min")).intValue();
        int max = ((Number) rows.get(0).get("max")).intValue();
        return max <= min ? min : ThreadLocalRandom.current().nextInt(min, max + 1);
    }

    public long start(UUID saleId, String step, int delayMs) {
        return db.queryForObject("INSERT INTO process.history(sale_id,step,outcome,delay_ms) VALUES (?,?,'RUNNING',?) RETURNING id",
                Long.class, saleId, step, delayMs);
    }

    /** Best effort: a journal failure must not turn a finished step into an error. */
    public void finish(long id, String outcome, String detail, long durationMs) {
        try {
            db.update("UPDATE process.history SET outcome=?,detail=?,duration_ms=?,finished_at=now() WHERE id=?",
                    outcome, detail == null ? null : detail.substring(0, Math.min(500, detail.length())), (int) durationMs, id);
        } catch (RuntimeException e) {
            log.warn("process_history_update_failed id={} error={}", id, e.toString());
        }
    }
}
