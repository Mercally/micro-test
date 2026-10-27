package com.example.docssimulator.service;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicInteger;

import com.example.docssimulator.api.TransientWorkflowException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class FailureSimulator {

    private final int failuresBeforeSuccess;
    private final double failureProbability;
    private final ConcurrentHashMap<String, AtomicInteger> attempts = new ConcurrentHashMap<>();

    public FailureSimulator(
            @Value("${app.simulator.failures-before-success:0}") int failuresBeforeSuccess,
            @Value("${app.simulator.failure-probability:0.2}") double failureProbability) {
        if (failuresBeforeSuccess < 0 || failureProbability < 0 || failureProbability > 1) {
            throw new IllegalArgumentException("Configuración inválida para el simulador de fallos");
        }
        this.failuresBeforeSuccess = failuresBeforeSuccess;
        this.failureProbability = failureProbability;
    }

    public void beforeCall(String step) {
        int attempt = attempts.computeIfAbsent(step, ignored -> new AtomicInteger()).incrementAndGet();
        if (attempt <= failuresBeforeSuccess
                || ThreadLocalRandom.current().nextDouble() < failureProbability) {
            throw new TransientWorkflowException(step);
        }
    }
}