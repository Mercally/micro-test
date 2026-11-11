# Laboratorio de compras asíncronas

Micro Spring Boot/Java 17: aceptación transaccional con outbox, documentos,
pólizas y PDF en S3. Laboratorio local con API Gateway autenticado, NLB, Istio,
mTLS, PostgreSQL, Lambda publisher, SQS, Pipes, Step Functions, HPA y Karpenter.

- **[LocalStack: arranque, operación y pruebas](localstack/README.md)** — entrada única local.
- **[AWS/CloudFormation: siguiente etapa](aws/README.md)** — diseño; generación y despliegue pendientes.
- **[Frontend Angular de ventas](frontend/README.md)** — interfaz local para crear ventas y consultar su estado.

```text
Cliente → API Gateway/JWT → NLB → Istio → aceptación + outbox PostgreSQL
                              ↑
Outbox → publisher → SQS → Pipe → Step Functions HTTP → micro → PDF/S3
```

Step Functions llama NLB directamente. NLB distribuye conexiones; Istio enruta
por servicio/ruta. Publisher y authorizer usan Lambda; workflow no la utiliza
como transporte. Un micro implementa actualmente varios pasos lógicos.

## Código de aplicación

`src/` contiene API, persistencia, generación PDF y pruebas. `Dockerfile` construye
imagen del micro. `workflow.http` permite ejercitar endpoints individuales;
flujo asíncrono completo usa `localstack/.state/business/purchase.http`, generado
por lanzador. No confundir HTTP 202 con compra terminada: consultar hasta COMPLETED.

Pruebas unitarias: `mvn test` con JDK 17+. Pruebas PostgreSQL requieren base
exclusiva de test; consultar condiciones en tests antes de habilitarlas.

Infraestructura AWS y Compose antiguos fueron retirados del árbol activo;
permanecen en historial Git. No mezclar instrucciones de commits anteriores
con arranque actual. Reportes, credenciales y estado de emuladores no se versionan.
