package com.example.docssimulator.service;

import java.lang.management.ManagementFactory;
import java.lang.ref.Reference;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/**
 * Step attempt journal and lab work simulation for the AWS flow. The journal is written outside step
 * transactions so rows survive failed attempts. The simulated work runs inside the step, exactly where
 * the real work happens (after idempotency checks): ALREADY_DONE, RECONCILED, out-of-order and injected
 * faults cost nothing, like a real idempotent service.
 */
@Service
public class ProcessHistory {
    private static final Logger log = LoggerFactory.getLogger(ProcessHistory.class);
    private static volatile long sink; // keeps the CPU loop from being optimized away
    private final JdbcTemplate db;

    public ProcessHistory(JdbcTemplate db) { this.db = db; }

    /** Simulated work for one step: I/O wait, CPU time and memory held. {@code used} = the step really acted. */
    public record Work(int delayMs, int cpuMs, int memoryMb, java.util.concurrent.atomic.AtomicBoolean used) {
        public Work(int delayMs, int cpuMs, int memoryMb) {
            this(delayMs, cpuMs, memoryMb, new java.util.concurrent.atomic.AtomicBoolean());
        }

        public static Work none() { return new Work(0, 0, 0); }

        public boolean wasUsed() { return used.get(); }
    }

    /** Read live from process.lab_settings so SQL or panel changes apply to the next step. */
    public Work work() {
        var rows = db.queryForList("""
                SELECT step_delay_min_ms AS min, step_delay_max_ms AS max, step_cpu_ms AS cpu, step_memory_mb AS mem
                FROM process.lab_settings""");
        if (rows.isEmpty()) return Work.none();
        var r = rows.get(0);
        int min = ((Number) r.get("min")).intValue();
        int max = ((Number) r.get("max")).intValue();
        int delay = max <= min ? min : ThreadLocalRandom.current().nextInt(min, max + 1);
        return new Work(delay, ((Number) r.get("cpu")).intValue(), ((Number) r.get("mem")).intValue());
    }

    /**
     * Holds memoryMb (pages touched so the pod's RSS really grows), burns cpuMs of this thread's CPU
     * time, then waits delayMs. CPU is measured as CPU time, not wall time: when the node is saturated
     * the step takes longer, like real work would.
     */
    public static void simulate(Work work) throws InterruptedException {
        work.used().set(true);
        byte[] held = work.memoryMb() > 0 ? new byte[work.memoryMb() * 1024 * 1024] : null;
        if (held != null) for (int i = 0; i < held.length; i += 4096) held[i] = 1;
        burnCpu(work.cpuMs());
        Thread.sleep(work.delayMs());
        Reference.reachabilityFence(held);
    }

    static void burnCpu(int cpuMs) {
        if (cpuMs <= 0) return;
        var threads = ManagementFactory.getThreadMXBean();
        boolean cpuTime = threads.isCurrentThreadCpuTimeSupported();
        long target = (cpuTime ? threads.getCurrentThreadCpuTime() : System.nanoTime()) + cpuMs * 1_000_000L;
        long x = 0;
        do {
            for (int i = 0; i < 20_000; i++) x += (x ^ i) * 31 + i;
        } while ((cpuTime ? threads.getCurrentThreadCpuTime() : System.nanoTime()) < target);
        sink = x;
    }

    public long start(UUID saleId, String step, Work work) {
        return db.queryForObject("""
                INSERT INTO process.history(sale_id,step,outcome,delay_ms,cpu_ms,memory_mb)
                VALUES (?,?,'RUNNING',?,?,?) RETURNING id""",
                Long.class, saleId, step, work.delayMs(), work.cpuMs(), work.memoryMb());
    }

    /**
     * Best effort: a journal failure must not turn a finished step into an error. When the step did not act
     * (already done, reconciled, failed before acting) the planned work is recorded as 0.
     */
    public void finish(long id, String outcome, String detail, long durationMs, boolean worked) {
        try {
            db.update("""
                    UPDATE process.history SET outcome=?,detail=?,duration_ms=?,finished_at=now(),
                      delay_ms=CASE WHEN ? THEN delay_ms ELSE 0 END,cpu_ms=CASE WHEN ? THEN cpu_ms ELSE 0 END,
                      memory_mb=CASE WHEN ? THEN memory_mb ELSE 0 END WHERE id=?""",
                    outcome, detail == null ? null : detail.substring(0, Math.min(500, detail.length())), (int) durationMs,
                    worked, worked, worked, id);
        } catch (RuntimeException e) {
            log.warn("process_history_update_failed id={} error={}", id, e.toString());
        }
    }
}
