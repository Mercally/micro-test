package com.example.docssimulator.config;

import java.net.URI;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import software.amazon.awssdk.auth.credentials.DefaultCredentialsProvider;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;

@Configuration
public class AwsS3Configuration {

    @Bean
    S3Client s3Client(@Value("${app.s3.region}") String region,
                      @Value("${app.s3.endpoint:}") String endpoint,
                      @Value("${app.lab.purchases-enabled:false}") boolean localLab) {
        if (localLab) requireLocalEndpoint(endpoint);
        var builder = S3Client.builder()
                .region(Region.of(region))
                .credentialsProvider(localLab ? StaticCredentialsProvider.create(AwsBasicCredentials.create("test", "test"))
                        : DefaultCredentialsProvider.create())
            .httpClientBuilder(UrlConnectionHttpClient.builder())
                .serviceConfiguration(S3Configuration.builder().pathStyleAccessEnabled(true).build());
        if (!endpoint.isBlank()) {
            builder.endpointOverride(URI.create(endpoint));
        }
        return builder.build();
    }

    @Bean
    S3Presigner s3Presigner(@Value("${app.s3.region}") String region,
                            @Value("${app.s3.presign-endpoint:${app.s3.endpoint:}}") String endpoint,
                            @Value("${app.lab.purchases-enabled:false}") boolean localLab) {
        if (localLab) requireLocalEndpoint(endpoint);
        var builder = S3Presigner.builder()
                .region(Region.of(region))
                .credentialsProvider(localLab ? StaticCredentialsProvider.create(AwsBasicCredentials.create("test", "test"))
                        : DefaultCredentialsProvider.create())
                .serviceConfiguration(S3Configuration.builder().pathStyleAccessEnabled(true).build());
        if (!endpoint.isBlank()) {
            builder.endpointOverride(URI.create(endpoint));
        }
        return builder.build();
    }

    private void requireLocalEndpoint(String endpoint) {
        URI uri = URI.create(endpoint);
        String host = uri.getHost();
        boolean allowed = host != null && (java.util.Set.of("localhost", "127.0.0.1", "100.100.154.68", "localhost.localstack.cloud").contains(host)
                || host.matches("10\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}")
                || host.matches("172\\.(1[6-9]|2[0-9]|3[01])\\.[0-9]{1,3}\\.[0-9]{1,3}")
                || host.matches("192\\.168\\.[0-9]{1,3}\\.[0-9]{1,3}"));
        if (!allowed || !"http".equals(uri.getScheme()) || uri.getPort() != 4566
                || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null
                || !(uri.getPath().isEmpty() || "/".equals(uri.getPath()))) {
            throw new IllegalArgumentException("Local purchase lab requires explicit LocalStack S3 endpoint on port 4566");
        }
    }
}
