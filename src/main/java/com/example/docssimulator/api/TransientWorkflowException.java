package com.example.docssimulator.api;

public class TransientWorkflowException extends RuntimeException {

    public TransientWorkflowException(String step) {
        super("Fallo transitorio simulado en " + step);
    }
}