# Secuencia — compra exitosa con procesamiento asíncrono

Caso feliz de extremo a extremo: aceptación síncrona (202), publicación outbox,
consumo SQS con patrón lease (N02), Step Functions con 3 pasos idempotentes por
HTTP Tasks privadas, cierre del mensaje y borrado del archivo temporal.
Componentes y fronteras: [ARQUITECTURA-C4.md](ARQUITECTURA-C4.md).
Definición real: `StateMachine.Definition` en [04-messaging.yaml](../cloudformation/04-messaging.yaml).

## Diagrama

```mermaid
sequenceDiagram
  autonumber
  actor U as Usuario
  box rgb(235,245,255) Borde público AWS
    participant GW as API Gateway
  end
  box rgb(235,255,240) VPC · EKS (mTLS Istio)
    participant API as API pod
    participant WK as Worker pod
    participant EXT as CM / Aseguradora (mocks)
  end
  box rgb(255,245,230) Subred aislada
    participant DB as Aurora
  end
  box rgb(245,240,255) Servicios gestionados
    participant Q as SQS
    participant L as Lambda
    participant SF as Step Functions
    participant S3 as S3 temporal
  end

  %% --- Aceptación síncrona ---
  U->>GW: POST /api/v1/purchases
  GW->>API: VPC Link → NLB → Istio gateway → mTLS
  API->>DB: BEGIN · INSERT sale · INSERT outbox · COMMIT
  API-->>U: 202 Accepted {saleId}
  Note over U,API: La aceptación termina aquí. El resto es asíncrono.

  %% --- Publicación outbox ---
  loop Poll programado
    WK->>DB: SELECT outbox pendiente FOR UPDATE SKIP LOCKED
    WK->>Q: SendMessage {saleId, eventId, correlationId}
    WK->>DB: UPDATE outbox SET published_at · COMMIT
  end

  %% --- Consumo con lease ---
  L->>Q: ReceiveMessage (ESM, batch 1)
  Note over Q: Mensaje invisible (visibility 360 s > timeout SFN 300 s)
  L->>SF: StartExecution name=messageId-receiveCount<br/>input={body, receiptHandle, receiveCount, maxReceiveCount, releaseDelaySeconds}
  Note over L,SF: La Lambda espera (DescribeExecution cada 2 s): N Lambdas ocupadas = N ejecuciones

  %% --- Workflow: cada paso consulta estado, reconcilia y solo entonces actúa ---
  Note over SF,API: Cada paso: HTTP Task → Connection → Resource Gateway → NLB → Istio → API<br/>3 intentos por paso ante 408/429/5xx/timeout, backoff ×2 con jitter
  SF->>API: POST /steps/document (paso 1)
  API->>DB: lock venta · ¿DOCUMENT hecho y archivo presente?
  API->>S3: PutObject tmp/sales/{saleId}/document.pdf
  API->>DB: registra DOCUMENT · venta PROCESSING
  API-->>SF: 200 {outcome: DONE}
  SF->>API: POST /steps/cm (paso 2)
  API->>DB: lock venta · ¿CM_UPLOAD hecho?
  API->>EXT: GET CM /documents/{saleId} (reconciliación) → 404
  API->>S3: GetObject documento
  API->>EXT: PUT CM /documents/{saleId} → verifica con GET
  API->>DB: registra CM_UPLOAD
  API-->>SF: 200 {outcome: DONE}
  SF->>API: POST /steps/insurer (paso 3)
  API->>DB: lock venta · ¿INSURER hecho?
  API->>EXT: GET Aseguradora /policies/{saleId} (reconciliación) → 404
  API->>EXT: POST Aseguradora /policies/{saleId} → acuse {policyNumber}
  API->>DB: registra INSURER · venta COMPLETED
  API-->>SF: 200 {outcome: DONE}
  SF->>Q: DeleteMessage(receiptHandle)
  SF->>API: POST /steps/cleanup
  API->>DB: ¿venta COMPLETED?
  API->>S3: DeleteObject archivo temporal
  API-->>SF: 200
  Note over SF: Succeeded
  L->>SF: DescribeExecution → SUCCEEDED
  L-->>Q: batchItemFailures=[messageId] (el ESM no borra; la SFN ya lo borró)

  %% --- Consulta ---
  U->>GW: GET /api/v1/purchases/{saleId}
  GW->>API: VPC Link → NLB → Istio
  API->>DB: SELECT venta + process.steps
  API-->>U: 200 {status: COMPLETED, steps: [...]}
```

## Qué pasa si se repite una invocación

| Situación | Resultado |
|---|---|
| Mismo paso llamado otra vez (reintento de SFN, mensaje reentregado, ejecución duplicada) | `ALREADY_DONE` sin repetir trabajo |
| CM o Aseguradora guardaron pero el acuse se perdió (503) | El reintento consulta primero → `RECONCILED`, no reenvía |
| Dos ejecuciones simultáneas de la misma venta | Se serializan por lock de fila; la segunda ve el trabajo hecho |
| Paso fuera de orden | 409, sin efectos |
| Archivo temporal ausente antes del paso 2 | 409 → reintento global → paso 1 lo regenera |
| `cleanup` antes de COMPLETED | 409, archivo conservado |
| `DeleteMessage` falla tras el éxito | Reentrega → los tres pasos responden `ALREADY_DONE` |
| Último intento global agotado | `MarkFailed` (nunca degrada COMPLETED) → mensaje liberado → DLQ → log y alarma CloudWatch |

Cubierto por `ProcessIntegrationTest` (PostgreSQL real, S3 y externos simulados con
acuse perdido): 7/7 en local, 2026-11-19. Pendiente: prueba en AWS.

## Tiempos que deben encajar

| Parámetro | Valor | Regla |
|---|---|---|
| Timeout por HTTP Task | 20 s | ≤ 60 s (límite HTTP Task) |
| Intentos por paso | 3 (1 + 2 reintentos) | "Intento < 3?" del diseño |
| Timeout ejecución SFN | 300 s | Peor caso: 3 pasos × 3 intentos + MarkFailed |
| Timeout Lambda consumidora | 330 s | > timeout SFN (espera su ejecución) y < visibility timeout |
| Visibility timeout SQS | 360 s | > timeout SFN; si no, otra Lambda inicia ejecución duplicada (inofensiva pero costosa) |
| `releaseDelaySeconds` | 30 s (parámetro) | Espera antes del reintento global |
| `maxReceiveCount` | 3 | Tercera entrega fallida → DLQ |
| Timeout HTTP a CM/Aseguradora | 2 s conexión / 5 s lectura | < timeout HTTP Task |

## Pendientes

1. Verificar en AWS si el ESM reduce concurrencia al recibir `batchItemFailures`.
2. Credencial del workflow en la Connection y su AuthorizationPolicy (D07).
3. Validar ASL con `aws stepfunctions validate-state-machine-definition` (no crea recursos);
   cfn-lint ya valida su estructura dentro de la plantilla.
