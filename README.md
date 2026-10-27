# Docs Simulator

Microservicio Spring Boot para simular los pasos de venta de un seguro, generación/liberación de documentos y creación de póliza. Usa una base PostgreSQL con un esquema por endpoint y S3 (LocalStack en desarrollo).

## Requisitos y arranque local

- Docker Desktop con Docker Compose.
- Java 17 y Maven 3.9+ si se ejecuta fuera de contenedores.

Inicia PostgreSQL, LocalStack y el servicio:

```bash
docker compose up --build
```

El servicio queda en `http://localhost:8080`; health check en `/actuator/health`. PostgreSQL se publica en `localhost:5432` y S3 de LocalStack en `localhost:4566`.

## Endpoints

Todos reciben JSON y devuelven el estado y los identificadores del paso. Los campos UUID se obtienen de la respuesta del paso anterior.

| Endpoint | Operación | Esquema PostgreSQL |
| --- | --- | --- |
| `POST /api/v1/endpoint0/sales` | Registra venta en estado `PENDING` | `endpoint0` |
| `POST /api/v1/endpoint1/documents` | Genera y persiste un PDF de prueba | `endpoint1` |
| `POST /api/v1/endpoint2/policies` | Crea póliza en estado `INITIAL` | `endpoint2` |
| `POST /api/v1/endpoint3/documents/upload` | Carga el PDF en S3 | `endpoint3` |
| `POST /api/v1/endpoint4/policies/complete` | Valida pasos previos y completa la póliza | `endpoint4` |
| `POST /api/v1/endpoint5/documents/release` | Registra liberación y devuelve URL prefirmada de 15 minutos | `endpoint5` |

Ejemplo para iniciar la venta:

```json
{
  "externalReference": "venta-demo-001",
  "customerName": "Cliente Demo",
  "customerEmail": "cliente@example.com",
  "productCode": "AUTO-01"
}
```

Los pasos 1 y 2 reciben `{ "saleId": "<uuid>" }`. El paso 3 y el paso 5 reciben `{ "saleId": "<uuid>", "documentId": "<uuid>" }`. El paso 4 recibe `{ "saleId": "<uuid>", "policyId": "<uuid>" }`.

## Fallos y reintentos

Cada endpoint puede devolver HTTP 503 aleatoriamente. Configura `SIMULATOR_FAILURE_PROBABILITY` entre `0` y `1`. `SIMULATOR_FAILURES_BEFORE_SUCCESS=N` fuerza los primeros N intentos fallidos de cada endpoint desde el arranque; por ejemplo, `4` permite verificar que Step Functions reintente más de tres veces. Los errores simulados ocurren antes de modificar datos.

Para probar cuatro fallos deterministas en Windows PowerShell, antes de levantar Compose:

```powershell
$env:SIMULATOR_FAILURES_BEFORE_SUCCESS = "4"
$env:SIMULATOR_FAILURE_PROBABILITY = "0"
docker compose up --build
```

La definición desplegable de Step Functions está en `statemachine/docs-orchestrator.asl.json`. Sustituye `YOUR_API_ID` y `YOUR_REGION` por el hostname del API Gateway que exponga el servicio. Cada tarea tiene siete reintentos con backoff exponencial y termina en `WorkflowFailed` cuando se agotan.

## Configuración S3 en AWS

En despliegues reales, configura `AWS_REGION`, `S3_BUCKET` y credenciales mediante el proveedor estándar de AWS (por ejemplo, un IAM role). `S3_ENDPOINT_URL` solo se necesita para S3 compatible/LocalStack; `S3_PRESIGN_ENDPOINT_URL` permite que las URL firmadas usen un hostname accesible por el consumidor. El bucket debe permitir `HeadBucket`, `CreateBucket` cuando todavía no exista y `PutObject`; el rol que consuma las URL necesita acceso a `GetObject`. Para producción conviene crear el bucket por infraestructura, restringir el rol y proteger el API Gateway.

Los pasos son idempotentes por referencia externa o por venta. El PDF se conserva en PostgreSQL hasta completar la carga; la URL de descarga es temporal y se renueva en cada llamada de liberación.