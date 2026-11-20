package com.example.docssimulator;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class DocsSimulatorApplication {

    public static void main(String[] args) {
        SpringApplication.run(DocsSimulatorApplication.class, args);
    }
}