package com.example.docssimulator.service;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.example.docssimulator.api.TransientWorkflowException;
import org.junit.jupiter.api.Test;

class FailureSimulatorTest {

    @Test
    void allowsMoreThanThreeRetriesBeforeSuccess() {
        FailureSimulator simulator = new FailureSimulator(4, 0.0);

        for (int attempt = 0; attempt < 4; attempt++) {
            assertThrows(TransientWorkflowException.class, () -> simulator.beforeCall("endpoint3"));
        }

        assertDoesNotThrow(() -> simulator.beforeCall("endpoint3"));
    }
}