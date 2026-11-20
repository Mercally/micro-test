package com.example.docssimulator.service;

import java.util.Map;
import java.util.UUID;

import com.example.docssimulator.api.Requests;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/** Step journal, DB-configured pacing and the sale list (UI tree), through the real HTTP layer. */
@SpringBootTest(properties = {"app.simulator.failure-probability=0", "app.lab.purchases-enabled=true",
        "app.s3.endpoint=http://127.0.0.1:4566", "logging.level.root=WARN"})
@EnabledIfEnvironmentVariable(named = "LAB_TEST_POSTGRES", matches = "true")
@AutoConfigureMockMvc
class ProcessHistoryIntegrationTest {
    @Autowired PurchaseService purchases;
    @Autowired JdbcTemplate db;
    @Autowired MockMvc http;
    @MockitoBean S3Storage storage;
    @MockitoBean ExternalSystems external;

    @BeforeEach
    void setUp() {
        assertEquals("docs_business_test", db.queryForObject("SELECT current_database()", String.class),
                "Tests must never truncate a lab/user database");
        db.execute("TRUNCATE process.history,process.steps,endpoint0.lab_faults,endpoint0.outbox,endpoint0.sales CASCADE");
        db.update("UPDATE process.lab_settings SET step_delay_min_ms=0,step_delay_max_ms=0,step_cpu_ms=0,step_memory_mb=0");
        when(storage.exists(anyString())).thenReturn(true);
        when(external.findCmDocument(any())).thenReturn("CM-x");
    }

    UUID sale(String reference, String failure) {
        var accepted = purchases.accept(new Requests.RegisterSaleRequest(reference, "Lab", "lab@example.com", "AUTO-01"), failure);
        return UUID.fromString(accepted.get("saleId").toString());
    }

    UUID eventId(UUID saleId) {
        return db.queryForObject("SELECT current_event_id FROM endpoint0.sales WHERE id=?", UUID.class, saleId);
    }

    void step(UUID saleId, String step, int status) throws Exception {
        http.perform(post("/api/v1/internal/purchases/{s}/steps/{step}", saleId, step)
                        .contentType("application/json").content("{\"eventId\":\"" + eventId(saleId) + "\"}"))
                .andExpect(status().is(status));
    }

    @Test
    void everyAttemptIsJournaledIncludingFailuresThatRollBack() throws Exception {
        UUID saleId = sale("history", "cm:transient:1");
        step(saleId, "document", 200);
        step(saleId, "cm", 503);
        step(saleId, "cm", 200);
        step(saleId, "cm", 200);

        var rows = db.queryForList("SELECT step,outcome,detail,duration_ms FROM process.history WHERE sale_id=? ORDER BY id", saleId);
        assertEquals(4, rows.size());
        assertEquals(Map.of("step", "document", "outcome", "DONE"), Map.of("step", rows.get(0).get("step"), "outcome", rows.get(0).get("outcome")));
        assertEquals("ERROR", rows.get(1).get("outcome"));
        assertTrue(rows.get(1).get("detail").toString().contains("cm"));
        assertEquals("RECONCILED", rows.get(2).get("outcome"));
        assertEquals("ALREADY_DONE", rows.get(3).get("outcome"));
        assertTrue(rows.stream().allMatch(r -> r.get("duration_ms") != null), "no attempt left RUNNING");
    }

    @Test
    void delayComesFromDatabaseAndAppliesOnlyToBusinessSteps() throws Exception {
        db.update("UPDATE process.lab_settings SET step_delay_min_ms=300,step_delay_max_ms=300");
        UUID saleId = sale("paced", null);
        long started = System.nanoTime();
        step(saleId, "document", 200);
        assertTrue((System.nanoTime() - started) / 1_000_000 >= 300);
        step(saleId, "fail", 200);
        assertEquals(java.util.List.of(300, 0),
                db.queryForList("SELECT delay_ms FROM process.history WHERE sale_id=? ORDER BY id", Integer.class, saleId));
    }

    @Test
    void simulatedWorkBurnsRealCpuTimeHoldsMemoryAndIsJournaled() throws Exception {
        db.update("UPDATE process.lab_settings SET step_cpu_ms=200,step_memory_mb=8");
        var threads = java.lang.management.ManagementFactory.getThreadMXBean();
        long cpuBefore = threads.getCurrentThreadCpuTime();
        ProcessHistory.simulate(new ProcessHistory.Work(0, 150, 4));
        assertTrue((threads.getCurrentThreadCpuTime() - cpuBefore) / 1_000_000 >= 150, "CPU time, not wall time");

        UUID saleId = sale("worked", null);
        step(saleId, "document", 200);
        var row = db.queryForMap("SELECT cpu_ms,memory_mb,duration_ms FROM process.history WHERE sale_id=?", saleId);
        assertEquals(200, row.get("cpu_ms"));
        assertEquals(8, row.get("memory_mb"));
        assertTrue(((Number) row.get("duration_ms")).intValue() >= 200);
        http.perform(get("/api/v1/purchases").param("limit", "1"))
                .andExpect(jsonPath("$[0].history[0].cpuMs").value(200))
                .andExpect(jsonPath("$[0].history[0].memoryMb").value(8));
    }

    @Test
    void listReturnsLatestSalesWithOutboxAndHistory() throws Exception {
        UUID older = sale("older", null);
        UUID newer = sale("newer", null);
        step(newer, "document", 200);
        http.perform(get("/api/v1/purchases").param("limit", "1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].saleId").value(newer.toString()))
                .andExpect(jsonPath("$[0].externalReference").value("newer"))
                .andExpect(jsonPath("$[0].outbox[0].generation").value(1))
                .andExpect(jsonPath("$[0].history[0].step").value("document"))
                .andExpect(jsonPath("$[0].history[0].outcome").value("DONE"))
                .andExpect(jsonPath("$[0].history[0].sale_id").doesNotExist());
        http.perform(get("/api/v1/purchases"))
                .andExpect(jsonPath("$.length()").value(2))
                .andExpect(jsonPath("$[1].saleId").value(older.toString()))
                .andExpect(jsonPath("$[1].history.length()").value(0));
    }

    @Test
    void settingsRejectRangesBeyondTheStepTimeout() {
        assertThrows(RuntimeException.class, () -> db.update("UPDATE process.lab_settings SET step_delay_max_ms=20000"));
        assertThrows(RuntimeException.class, () -> db.update("UPDATE process.lab_settings SET step_delay_min_ms=5,step_delay_max_ms=1"));
    }
}
