FROM maven:3.9.9-eclipse-temurin-17 AS build
WORKDIR /workspace

COPY pom.xml .
RUN mvn -B dependency:go-offline

COPY src ./src
RUN mvn -B package -DskipTests

# Descargar el agente de OpenTelemetry
FROM alpine:3.20 AS otel
RUN apk add --no-cache curl ca-certificates
ARG OTEL_VERSION=2.22.0
RUN curl -fSL \
    "https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_VERSION}/opentelemetry-javaagent.jar" \
    -o /opentelemetry-javaagent.jar

# Imagen final
FROM eclipse-temurin:17-jre
WORKDIR /app

COPY --from=build \
    /workspace/target/docs-simulator-0.0.1-SNAPSHOT.jar \
    app.jar

COPY --from=otel \
    /opentelemetry-javaagent.jar \
    opentelemetry-javaagent.jar

EXPOSE 8080

ENTRYPOINT [ "java", "-javaagent:/app/opentelemetry-javaagent.jar", "-jar", "/app/app.jar" ]