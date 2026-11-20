package com.example.docssimulator.service;

import java.util.Map;
import java.util.function.Consumer;

import com.example.docssimulator.api.PurchaseException;
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
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.GetQueueAttributesResponse;
import software.amazon.awssdk.services.sqs.model.QueueAttributeName;
import software.amazon.awssdk.services.sqs.model.StartMessageMoveTaskRequest;
import software.amazon.awssdk.services.sqs.model.StartMessageMoveTaskResponse;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/** Test panel API against real PostgreSQL; SQS is a mock (its real behavior is exercised in AWS). */
@SpringBootTest(properties = {"app.simulator.failure-probability=0", "app.lab.purchases-enabled=true",
        "app.s3.endpoint=http://127.0.0.1:4566", "logging.level.root=WARN",
        "app.lab.panel.enabled=true", "app.lab.panel.queue-url=https://sqs.x/1/micro-test-purchases",
        "app.lab.panel.dlq-url=https://sqs.x/1/micro-test-purchases-dlq", "app.lab.panel.max-concurrency=2"})
@EnabledIfEnvironmentVariable(named = "LAB_TEST_POSTGRES", matches = "true")
@AutoConfigureMockMvc
class LabPanelIntegrationTest {
    @Autowired PurchaseService purchases;
    @Autowired JdbcTemplate db;
    @Autowired MockMvc http;
    @MockitoBean S3Storage storage;
    @MockitoBean SqsClient sqs;

    @BeforeEach
    @SuppressWarnings("unchecked")
    void setUp() {
        assertEquals("docs_business_test", db.queryForObject("SELECT current_database()", String.class),
                "Tests must never truncate a lab/user database");
        db.execute("TRUNCATE process.history,process.steps,endpoint0.lab_faults,endpoint0.outbox,endpoint0.sales CASCADE");
        db.update("UPDATE process.lab_settings SET step_delay_min_ms=0,step_delay_max_ms=10000");
        when(sqs.getQueueAttributes(any(Consumer.class))).thenReturn(GetQueueAttributesResponse.builder().attributes(Map.of(
                QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES, "7", QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES_NOT_VISIBLE, "2",
                QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES_DELAYED, "0",
                QueueAttributeName.QUEUE_ARN, "arn:aws:sqs:us-east-1:1:micro-test-purchases-dlq")).build());
    }

    @Test
    void settingsAreReadAndValidated() throws Exception {
        http.perform(get("/api/v1/lab/settings")).andExpect(jsonPath("$.stepDelayMaxMs").value(10000));
        http.perform(put("/api/v1/lab/settings").contentType("application/json").content("{\"stepDelayMinMs\":500,\"stepDelayMaxMs\":2000}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.stepDelayMinMs").value(500));
        http.perform(put("/api/v1/lab/settings").contentType("application/json").content("{\"stepDelayMinMs\":0,\"stepDelayMaxMs\":16000}"))
                .andExpect(status().isBadRequest());
        assertEquals(2000, db.queryForObject("SELECT step_delay_max_ms FROM process.lab_settings", Integer.class));
    }

    @Test
    void burstAcceptsAllSalesWithTheirFaultInOneTransaction() throws Exception {
        http.perform(post("/api/v1/lab/burst").contentType("application/json").content("{\"count\":5,\"failure\":\"cm:transient:12\"}"))
                .andExpect(status().isAccepted()).andExpect(jsonPath("$.count").value(5));
        assertEquals(5, db.queryForObject("SELECT count(*) FROM endpoint0.outbox WHERE published_at IS NULL", Integer.class));
        assertEquals(5, db.queryForObject("SELECT count(*) FROM endpoint0.lab_faults WHERE step='cm' AND remaining=12", Integer.class));

        http.perform(post("/api/v1/lab/burst").contentType("application/json").content("{\"count\":3,\"failure\":\"cm:transient:99\"}"))
                .andExpect(status().isBadRequest());
        http.perform(post("/api/v1/lab/burst").contentType("application/json").content("{\"count\":1001}"))
                .andExpect(status().isBadRequest());
        assertEquals(5, db.queryForObject("SELECT count(*) FROM endpoint0.sales", Integer.class), "invalid burst writes nothing");
    }

    @Test
    void systemShowsQueuesOutboxSalesAndLimits() throws Exception {
        purchases.accept(new Requests.RegisterSaleRequest("sys-1", "Lab", "lab@example.com", "AUTO-01"), null);
        http.perform(get("/api/v1/lab/system"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.queues.main.visible").value(7))
                .andExpect(jsonPath("$.queues.dlq.inFlight").value(2))
                .andExpect(jsonPath("$.queues.dlq.name").value("micro-test-purchases-dlq"))
                .andExpect(jsonPath("$.outboxPending").value(1))
                .andExpect(jsonPath("$.sales.ACCEPTED").value(1))
                .andExpect(jsonPath("$.kubernetes.available").value(false))
                .andExpect(jsonPath("$.limits.maxConcurrency").value(2));
    }

    @Test
    void redriveMovesDlqMessagesBackToTheSourceQueue() throws Exception {
        when(sqs.startMessageMoveTask(any(Consumer.class))).thenAnswer(i -> {
            var request = StartMessageMoveTaskRequest.builder();
            i.<Consumer<StartMessageMoveTaskRequest.Builder>>getArgument(0).accept(request);
            assertEquals("arn:aws:sqs:us-east-1:1:micro-test-purchases-dlq", request.build().sourceArn());
            return StartMessageMoveTaskResponse.builder().taskHandle("h").build();
        });
        http.perform(post("/api/v1/lab/dlq/redrive")).andExpect(status().isAccepted());
        verify(sqs).startMessageMoveTask(any(Consumer.class));
    }

    @Test
    void faultCountsUpToTwentyCoverEveryRetryScenario() {
        purchases.accept(new Requests.RegisterSaleRequest("f-20", "Lab", "lab@example.com", "AUTO-01"), "insurer:transient:20");
        assertThrows(PurchaseException.class,
                () -> purchases.accept(new Requests.RegisterSaleRequest("f-21", "Lab", "lab@example.com", "AUTO-01"), "insurer:transient:21"));
    }
}
