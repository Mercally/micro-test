package com.example.docssimulator.api;

import java.util.UUID;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

public final class Requests {

    private Requests() {
    }

    public record RegisterSaleRequest(
            @NotBlank String externalReference,
            @NotBlank String customerName,
            @NotBlank
            @Email String customerEmail,
            @NotBlank String productCode) {

    }

    public record UpdateSaleStatusRequest(
            @NotNull UUID saleId,
            @NotBlank String status
            ) {

    }

    public record SaleStepRequest(@NotNull UUID saleId) {

    }

    public record DocumentStepRequest(@NotNull UUID saleId, @NotNull UUID documentId) {

    }

    public record PolicyStepRequest(@NotNull UUID saleId, @NotNull UUID policyId) {

    }
}
