package com.example.docssimulator.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.mockito.Answers;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import software.amazon.awssdk.services.sqs.SqsClient;

import static org.junit.jupiter.api.Assertions.*;

/** Worker Deployment config (WORKER_ENABLED=true) wires the publisher with the SqsClient bean. */
@SpringBootTest(properties = {"app.worker.enabled=true",
        "app.worker.queue-url=https://sqs.us-east-1.amazonaws.com/000000000000/micro-test-purchases",
        "app.lab.purchases-enabled=true", "app.s3.endpoint=http://127.0.0.1:4566", "logging.level.root=WARN"})
@EnabledIfEnvironmentVariable(named = "LAB_TEST_POSTGRES", matches = "true")
class OutboxWorkerWiringTest {
    @Autowired ApplicationContext context;
    @MockitoBean S3Storage storage;
    @MockitoBean(answers = Answers.RETURNS_DEEP_STUBS) SqsClient sqs;

    @Test
    void workerProfileCreatesPublisher() {
        assertNotNull(context.getBean(OutboxPublisher.class));
    }
}
