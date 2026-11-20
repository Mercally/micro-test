package com.example.docssimulator.service;

import java.util.Map;
import java.util.UUID;
import java.util.function.Supplier;

import com.example.docssimulator.api.TransientWorkflowException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

/** HTTP clients for Content Manager and the insurer. Any transport/5xx error becomes a retryable 503. */
@Component
public class ExternalSystems {
    private final RestClient cm;
    private final RestClient insurer;

    public ExternalSystems(@Value("${app.external.cm-url}") String cmUrl,
                           @Value("${app.external.insurer-url}") String insurerUrl,
                           @Value("${app.external.connect-timeout-ms:2000}") int connectTimeout,
                           @Value("${app.external.read-timeout-ms:5000}") int readTimeout) {
        var factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(connectTimeout);
        factory.setReadTimeout(readTimeout);
        this.cm = RestClient.builder().baseUrl(cmUrl).requestFactory(factory).build();
        this.insurer = RestClient.builder().baseUrl(insurerUrl).requestFactory(factory).build();
    }

    /** Reconciliation: document id if CM already has it, else null. */
    public String findCmDocument(UUID saleId) {
        return find("cm", () -> cm.get().uri("/documents/{id}", saleId).retrieve().body(Map.class), "documentId");
    }

    public String storeCmDocument(UUID saleId, byte[] pdf) {
        return field("cm", () -> cm.put().uri("/documents/{id}", saleId).contentType(MediaType.APPLICATION_PDF)
                .body(pdf).retrieve().body(Map.class), "documentId");
    }

    /** Reconciliation: policy number if the insurer already received the request, else null. */
    public String findInsurerPolicy(UUID saleId) {
        return find("insurer", () -> insurer.get().uri("/policies/{id}", saleId).retrieve().body(Map.class), "policyNumber");
    }

    /** Returns the insurer acknowledgement (policy number). */
    public String sendInsurerPolicy(UUID saleId) {
        return field("insurer", () -> insurer.post().uri("/policies/{id}", saleId).retrieve().body(Map.class), "policyNumber");
    }

    private String find(String system, Supplier<Map<?, ?>> call, String name) {
        try {
            return field(system, call, name);
        } catch (TransientWorkflowException exception) {
            if (exception.getCause() instanceof HttpClientErrorException.NotFound) return null;
            throw exception;
        }
    }

    private String field(String system, Supplier<Map<?, ?>> call, String name) {
        try {
            Map<?, ?> body = call.get();
            Object value = body == null ? null : body.get(name);
            if (value == null) throw new TransientWorkflowException(system + ": respuesta sin " + name);
            return value.toString();
        } catch (RestClientException exception) {
            var failure = new TransientWorkflowException(system);
            failure.initCause(exception);
            throw failure;
        }
    }
}
