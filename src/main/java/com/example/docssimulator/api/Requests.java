package com.example.docssimulator.api;

import java.util.UUID;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

public final class Requests {

    private Requests() {
    }

    public record RegisterSaleRequest(
            @NotBlank @Size(max = 120) String externalReference,
            @NotBlank @Size(max = 200) String customerName,
            @NotBlank
            @Email @Size(max = 320) String customerEmail,
            @NotBlank @Size(max = 80) String productCode) {

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
