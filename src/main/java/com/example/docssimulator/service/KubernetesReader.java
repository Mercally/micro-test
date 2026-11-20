package com.example.docssimulator.service;

import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import java.time.Duration;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;

/**
 * Read-only Kubernetes API access with the pod's service account (RBAC in aws/kubernetes/app.yaml).
 * ponytail: plain JDK HttpClient for four GETs; a client library if the panel ever writes to the cluster.
 */
@Component
public class KubernetesReader {
    private static final Path SA = Path.of("/var/run/secrets/kubernetes.io/serviceaccount");
    private final ObjectMapper json;
    private HttpClient client;

    public KubernetesReader(ObjectMapper json) { this.json = json; }

    public boolean available() {
        return System.getenv("KUBERNETES_SERVICE_HOST") != null && Files.exists(SA.resolve("token"));
    }

    public JsonNode get(String path) throws Exception {
        String host = System.getenv("KUBERNETES_SERVICE_HOST");
        String port = System.getenv().getOrDefault("KUBERNETES_SERVICE_PORT", "443");
        var request = HttpRequest.newBuilder(URI.create("https://" + host + ":" + port + path))
                .header("Authorization", "Bearer " + Files.readString(SA.resolve("token")).trim()) // token rotates: read each call
                .timeout(Duration.ofSeconds(3)).GET().build();
        var response = client().send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) throw new IllegalStateException("kubernetes " + path + " -> " + response.statusCode());
        return json.readTree(response.body());
    }

    private synchronized HttpClient client() throws Exception {
        if (client == null) {
            var keyStore = KeyStore.getInstance(KeyStore.getDefaultType());
            keyStore.load(null, null);
            try (InputStream ca = Files.newInputStream(SA.resolve("ca.crt"))) {
                keyStore.setCertificateEntry("cluster-ca", CertificateFactory.getInstance("X.509").generateCertificate(ca));
            }
            var trust = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
            trust.init(keyStore);
            var ssl = SSLContext.getInstance("TLS");
            ssl.init(null, trust.getTrustManagers(), null);
            client = HttpClient.newBuilder().sslContext(ssl).connectTimeout(Duration.ofSeconds(2)).build();
        }
        return client;
    }
}
