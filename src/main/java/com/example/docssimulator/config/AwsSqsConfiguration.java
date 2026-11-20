package com.example.docssimulator.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.sqs.SqsClient;

/** SQS client for the outbox worker only. Credentials: default chain (Pod Identity on EKS). */
@Configuration
@ConditionalOnProperty(name = "app.worker.enabled", havingValue = "true")
public class AwsSqsConfiguration {

    @Bean
    SqsClient sqsClient(@Value("${app.worker.region}") String region) {
        return SqsClient.builder().region(Region.of(region)).httpClientBuilder(UrlConnectionHttpClient.builder()).build();
    }
}
