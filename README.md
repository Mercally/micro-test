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
| -------------------------------------------- | ------------------------------------------------------------ | ------------------ |
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

El laboratorio AWS privado está en [aws/README.md](aws/README.md). `statemachine/docs-orchestrator.asl.json` refleja su dispatcher Step Functions → SQS; sustituye `${QUEUE_URL}` por la cola creada. Su éxito confirma el encolado, no la ejecución de los endpoints. El micro todavía no implementa consumo SQS ni llamadas a Step Functions; `workflow.http` sigue siendo una prueba HTTP manual.

## Configuración S3 en AWS

En despliegues reales, configura `AWS_REGION`, `S3_BUCKET` y credenciales mediante el proveedor estándar de AWS (por ejemplo, un IAM role). `S3_ENDPOINT_URL` solo se necesita para S3 compatible/LocalStack; `S3_PRESIGN_ENDPOINT_URL` permite que las URL firmadas usen un hostname accesible por el consumidor. El arranque llama `HeadBucket` (requiere `s3:ListBucket`) e intenta `CreateBucket` si el bucket no existe. La carga necesita `s3:PutObject`; el rol que firma las URL necesita `s3:GetObject`. El laboratorio crea los buckets por infraestructura, concede permisos por rol y mantiene los servicios HTTP privados. No concede `CreateBucket` a los pods.

Los pasos son idempotentes por referencia externa o por venta. El PDF se conserva en PostgreSQL hasta completar la carga; la URL de descarga es temporal y se renueva en cada llamada de liberación.

Comandos

Listar buckets:
`aws --endpoint-url=http://localhost:4566 s3 ls`

Listar archivos en bucket:
`aws --endpoint-url=http://localhost:4566 s3 ls s3://docs-simulator/ --recursive`

Descargar archivo:
``aws --endpoint-url=http://localhost:4566 s3 cp s3://docs-simulator/sales/SALE_ID/DOCUMENT_ID.pdf  ./downloads/documento.pdf``

Build image:

AMD64
``docker buildx build --platform linux/amd64 -t docs-simulator:1.0.o --load .``

ARM64

`docker buildx build --platform linux/arm64 -t docs-simulator:1.0.o --load .`

Publish new docker image to ECR:

CLI:

```
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t 491085384897.dkr.ecr.us-east-1.amazonaws.com/demos/seguros:1.0.1 \
  --push .
```
