package com.example.docssimulator.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.sqs.SqsClient;

/** SQS client for the outbox worker only. Credentials: default chain (Pod Identity on EKS). */
@Configuration
@ConditionalOnExpression("${app.worker.enabled:false} or ${app.lab.panel.enabled:false}")
public class AwsSqsConfiguration {

    @Bean
    SqsClient sqsClient(@Value("${app.worker.region}") String region) {
        return SqsClient.builder().region(Region.of(region)).httpClientBuilder(UrlConnectionHttpClient.builder()).build();
    }
}
