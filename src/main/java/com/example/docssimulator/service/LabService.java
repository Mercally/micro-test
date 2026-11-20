package com.example.docssimulator.service;

import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import com.example.docssimulator.api.PurchaseException;
import com.example.docssimulator.api.Requests;
import com.fasterxml.jackson.databind.JsonNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.QueueAttributeName;

/** Test panel: lab knobs, live system view (queues, outbox, pods, nodes), burst load and DLQ redrive. */
@Service
@ConditionalOnProperty(name = "app.lab.panel.enabled", havingValue = "true")
public class LabService {
    public static final int MAX_BURST = 1000;
    private static final Logger log = LoggerFactory.getLogger(LabService.class);

    private final JdbcTemplate db;
    private final SqsClient sqs;
    private final KubernetesReader kubernetes;
    private final PurchaseService purchases;
    private final String queueUrl;
    private final String dlqUrl;
    private final String namespace;
    private final Map<String, Object> limits;

    public LabService(JdbcTemplate db, SqsClient sqs, KubernetesReader kubernetes, PurchaseService purchases,
                      @Value("${app.lab.panel.queue-url}") String queueUrl, @Value("${app.lab.panel.dlq-url}") String dlqUrl,
                      @Value("${app.lab.panel.namespace}") String namespace,
                      @Value("${app.lab.panel.max-concurrency}") int maxConcurrency,
                      @Value("${app.lab.panel.api-max-replicas}") int apiMaxReplicas,
                      @Value("${app.lab.panel.messages-per-pod}") int messagesPerPod,
                      @Value("${app.lab.panel.karpenter-cpu-limit}") int karpenterCpuLimit) {
        this.db = db;
        this.sqs = sqs;
        this.kubernetes = kubernetes;
        this.purchases = purchases;
        this.queueUrl = queueUrl;
        this.dlqUrl = dlqUrl;
        this.namespace = namespace;
        this.limits = Map.of("maxConcurrency", maxConcurrency, "apiMaxReplicas", apiMaxReplicas,
                "messagesPerPod", messagesPerPod, "karpenterCpuLimit", karpenterCpuLimit,
                "stepAttempts", 3, "deliveries", 3);
    }

    public Map<String, Object> settings() {
        return db.queryForMap("SELECT step_delay_min_ms AS \"stepDelayMinMs\", step_delay_max_ms AS \"stepDelayMaxMs\" FROM process.lab_settings");
    }

    public Map<String, Object> updateSettings(int minMs, int maxMs) {
        try {
            db.update("UPDATE process.lab_settings SET step_delay_min_ms=?,step_delay_max_ms=?", minMs, maxMs);
        } catch (DataIntegrityViolationException e) {
            throw new PurchaseException(400, "INVALID_SETTINGS", "Rango válido: 0 <= mínimo <= máximo <= 15000 ms");
        }
        return settings();
    }

    /** N sales accepted in one transaction: the worker then publishes them as an abrupt SQS burst. */
    @Transactional
    public Map<String, Object> burst(int count, String failure) {
        if (count < 1 || count > MAX_BURST) throw new PurchaseException(400, "INVALID_BURST", "Cantidad 1-" + MAX_BURST);
        String prefix = "BURST-" + LocalTime.now().format(DateTimeFormatter.ofPattern("HHmmss")) + "-" + (int) (Math.random() * 1000);
        for (int i = 1; i <= count; i++) {
            purchases.accept(new Requests.RegisterSaleRequest(prefix + "-" + i, "Burst " + i, "burst" + i + "@example.com", "AUTO-01"), failure);
        }
        log.info("lab_burst prefix={} count={} failure={}", prefix, count, failure);
        return Map.of("prefix", prefix, "count", count);
    }

    public Map<String, Object> system() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("queues", Map.of("main", queue(queueUrl), "dlq", queue(dlqUrl)));
        result.put("outboxPending", db.queryForObject("SELECT count(*) FROM endpoint0.outbox WHERE published_at IS NULL", Long.class));
        Map<String, Object> sales = new TreeMap<>();
        db.queryForList("SELECT status, count(*) AS n FROM endpoint0.sales GROUP BY status").forEach(r -> sales.put((String) r.get("status"), r.get("n")));
        result.put("sales", sales);
        result.put("running", db.queryForObject("SELECT count(*) FROM process.history WHERE outcome='RUNNING' AND started_at > now() - interval '1 minute'", Long.class));
        result.put("kubernetes", kubernetes());
        result.put("limits", limits);
        return result;
    }

    /** Moves every DLQ message back to its source queue (SQS message move task). */
    public Map<String, Object> redrive() {
        String dlqArn = sqs.getQueueAttributes(b -> b.queueUrl(dlqUrl).attributeNames(QueueAttributeName.QUEUE_ARN))
                .attributes().get(QueueAttributeName.QUEUE_ARN);
        String handle = sqs.startMessageMoveTask(b -> b.sourceArn(dlqArn)).taskHandle();
        log.info("lab_dlq_redrive taskHandle={}", handle);
        return Map.of("started", true);
    }

    private Map<String, Object> queue(String url) {
        if (url == null || url.isBlank()) return Map.of("configured", false);
        try {
            var a = sqs.getQueueAttributes(b -> b.queueUrl(url).attributeNames(
                    QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES, QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES_NOT_VISIBLE,
                    QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES_DELAYED)).attributes();
            return Map.of("configured", true, "name", url.substring(url.lastIndexOf('/') + 1),
                    "visible", Integer.parseInt(a.get(QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES)),
                    "inFlight", Integer.parseInt(a.get(QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES_NOT_VISIBLE)),
                    "delayed", Integer.parseInt(a.get(QueueAttributeName.APPROXIMATE_NUMBER_OF_MESSAGES_DELAYED)));
        } catch (RuntimeException e) {
            return Map.of("configured", true, "error", String.valueOf(e.getMessage()));
        }
    }

    private Map<String, Object> kubernetes() {
        if (!kubernetes.available()) return Map.of("available", false);
        try {
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("available", true);
            List<Map<String, Object>> deployments = new ArrayList<>();
            for (JsonNode d : kubernetes.get("/apis/apps/v1/namespaces/" + namespace + "/deployments").path("items")) {
                deployments.add(Map.of("name", d.path("metadata").path("name").asText(),
                        "desired", d.path("spec").path("replicas").asInt(), "ready", d.path("status").path("readyReplicas").asInt()));
            }
            result.put("deployments", deployments);
            Map<String, Integer> pending = new TreeMap<>();
            for (JsonNode p : kubernetes.get("/api/v1/namespaces/" + namespace + "/pods?fieldSelector=status.phase%3DPending").path("items")) {
                pending.merge(p.path("metadata").path("labels").path("app").asText("?"), 1, Integer::sum);
            }
            result.put("pendingPods", pending);
            List<Map<String, Object>> hpas = new ArrayList<>();
            for (JsonNode h : kubernetes.get("/apis/autoscaling/v2/namespaces/" + namespace + "/horizontalpodautoscalers").path("items")) {
                hpas.add(Map.of("name", h.path("metadata").path("name").asText(), "current", h.path("status").path("currentReplicas").asInt(),
                        "desired", h.path("status").path("desiredReplicas").asInt(),
                        "min", h.path("spec").path("minReplicas").asInt(1), "max", h.path("spec").path("maxReplicas").asInt()));
            }
            result.put("hpa", hpas);
            List<Map<String, Object>> nodes = new ArrayList<>();
            for (JsonNode n : kubernetes.get("/api/v1/nodes").path("items")) {
                JsonNode labels = n.path("metadata").path("labels");
                boolean ready = false;
                for (JsonNode c : n.path("status").path("conditions")) ready |= "Ready".equals(c.path("type").asText()) && "True".equals(c.path("status").asText());
                nodes.add(Map.of("name", n.path("metadata").path("name").asText(),
                        "pool", labels.has("karpenter.sh/nodepool") ? labels.path("karpenter.sh/nodepool").asText() : "base",
                        "capacityType", labels.path("karpenter.sh/capacity-type").asText(labels.path("eks.amazonaws.com/capacityType").asText("on-demand")),
                        "instanceType", labels.path("node.kubernetes.io/instance-type").asText(),
                        "ready", ready));
            }
            result.put("nodes", nodes);
            return result;
        } catch (Exception e) {
            return Map.of("available", false, "error", String.valueOf(e.getMessage()));
        }
    }
}
