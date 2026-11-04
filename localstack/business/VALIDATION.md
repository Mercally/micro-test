# Laptop: evidencia y pendientes — 2026-11-04

Destino único: `http://100.100.154.68:4566`, cuenta `000000000000`, `us-east-1`.
LocalStack observado: `2026.10.4:a39e30ef`, Pro, licencia activa, Docker Desktop
sobre WSL2 x86_64. No se consultó host anterior ni AWS real.

**Resultado: implementación entregada; flujo completo en laptop NO APROBADO.**
Falta acceso WSL autenticado para construir/desplegar micro y probar Kubernetes.
El usuario confirmó que ejecutará comandos WSL personalmente y necesita workers
locales reales. Se preparó Karpenter + Cluster API + CAPD/Docker en clúster adicional:
ocho recursos validados contra CRDs fijados y siete pruebas de guardias pasaron.
Build Docker, instalación, HTTP en workers y eliminación física están **PENDIENTES**.
Compras e Istio conservan EKS/k3d existente; CAPD no escala sus Managed Node Groups.
Evidencia nueva: [validación Karpenter estática](../reports/laptop/20261104T164201Z-43f995/karpenter-static.json).
SSH puerto 22 respondió, pero `mercally` fue rechazado; puertos 4510/4511 desde Mac
dieron timeout. No se modificaron firewall, Tailscale ni configuración global Docker.

| Comprobación ejecutada | Resultado | Alcance/evidencia |
|---|---|---|
| Inventario inicial | PASS | Diez stacks base CREATE_COMPLETE; micro-api y negocio ausentes. [status](../reports/laptop/20261104T152910Z-a653f0/status.json) |
| EKS y tres Managed Node Groups | API ACTIVE | Versión solicitada 1.34, labels/taints observados. **No demuestra Ready**. [lectura](../reports/laptop/20261104T154605Z-e84a8e/eks-api.json) |
| Lambda Python real → Secrets Manager → RDS | PASS | Paquete publisher real, SELECT a `docs` como `docs`. RDS no tiene tablas sales/outbox. [probe](../reports/laptop/20261104T153846Z-be83bd/probe.json) |
| CloudFormation / definición ASL | PASS estático/API | Tres templates sin errores cfn-lint; ValidateTemplate y ValidateStateMachineDefinition OK, cero diagnósticos. No demuestra ejecución del flujo. [definiciones](../reports/laptop/20261104T154109Z-4ca508/definitions.json) |
| Java | 11 PASS, 0 skipped | PostgreSQL 16.15 real aislado en Mac, Java 17.0.15. Nueve tests compras, uno S3 real, uno simulador. [componentes](../reports/laptop/20261104T154605Z-0ffd41/component-tests.json) |
| Python publisher/contratos | 6 PASS | PostgreSQL real; frontera SQS simulada. Cuatro publishers/20 eventos, crash después de envío, SQS fallido, JWT, normalizador y guardia de endpoints. Mismo reporte de componentes |
| Guardias existentes | 8 PASS | Endpoint explícito, rechazo externo/redirect, saneamiento credenciales y conservación de datos |
| PDF/S3/URL prefirmada | PASS de componentes | Java y PostgreSQL en Mac → S3 laptop; PDF real, contenido descargado idéntico, repetir upload conserva VersionId. No hubo API Gateway/Pipe/SFN en esta prueba |
| DLQ | PASS | Colas diagnósticas existentes, payload correlacionado tras relecturas. **No prueba errores SFN → DLQ**. [redrive](../reports/laptop/20261104T153135Z-2bc146/redrive.json) |
| IAM con identidad restringida | **FAIL** | Usuario recién creado sin políticas pudo HeadBucket. No se aprobó aislamiento IAM. Identidad temporal retirada. [fallo](../reports/laptop/20261104T153002Z-1fea84/iam.json) |
| Persistencia | **PENDIENTE** | health: disabled. No se reinició emulador ni se intentó pérdida de datos |

Reinspección del gateway en esta continuación: misma versión, persistencia
desactivada, diez stacks base conservados y negocio todavía ausente. Evidencia:
[status actualizado](../reports/laptop/20261104T163741Z-55007b/status.json).

Pruebas PostgreSQL verifican:

- 16 aceptaciones concurrentes: una venta, un evento; contenido distinto devuelve 409.
- Error real al insertar outbox revierte venta; fallo posterior revierte ambas escrituras.
- Requests HTTP 202/200/400/404/409 mediante MockMvc y DB real.
- Creación concurrente conserva documentId/PDF; repetición de upload evita otro PUT tras commit.
- PUT exitoso y fallo SQL posterior: reintento usa mismo key/bytes, un registro lógico.
- No completar antes de liberar documento; COMPLETED no retrocede.
- Reclamo de ejecución, estado FAILED, nueva generación y rechazo de propietario obsoleto.
- Leases vencidos en ACCEPTED/PROCESSING permiten recuperación explícita.

Fallos encontrados y corregidos durante pruebas: `python-dateutil` 4 era incompatible
con botocore; `scramp` 1.5 no exponía función requerida por pg8000 al negociar TLS.
Se fijaron versiones compatibles, se volvió a empaquetar y Lambda/RDS pasó. Los
reportes fallidos anteriores permanecen en sus propias sesiones como evidencia.

## Recursos modificados en laptop durante esta sesión

- Se conservaron los diez stacks base y sus datos.
- Se creó `micro-test-local-business-artifacts`: bucket versionado con paquetes Lambda.
- Se creó/actualizó `micro-test-local-business-probe`: Lambda sin disparador, rol y logs,
  utilizada para consulta SQL de conectividad. No inserta ventas ni crea schema.
- Pruebas de componentes dejaron PDFs versionados propios en bucket documentos.
- Prueba DLQ retiró únicamente su mensaje correlacionado; prueba IAM retiró usuario temporal.
- Stack `micro-test-local-business` **no desplegado**: falta URL real del gateway del micro.
- No se crearon tokens de API ni un `.http` con API ID ficticio. Existe plantilla y generador.

Código nuevo incluye aceptación/outbox, publisher, authorizer, transporte, ASL,
CloudFormation, despliegue WSL, generador `.http`, suite E2E y comparación de
persistencia. Sintaxis/contratos probados no sustituyen las pruebas pendientes.

## Pendientes para aceptación completa

| Prueba | Estado / cómo ejecutarla |
|---|---|
| Workers/pods Ready, scheduling en tres grupos | PENDIENTE: `wsl.py` en laptop |
| Micro contra RDS, migración aditiva y transporte Lambda → micro | PENDIENTE: `wsl.py` |
| Push/pull ECR por digest | PENDIENTE: `wsl.py` |
| Istio mTLS positivo/negativo y NetworkPolicy con controles | PENDIENTE: `wsl.py` |
| API JWT válida/ausente/firma incorrecta/expirada sobre API Gateway | PENDIENTE: `business.py test`; verificación JWT aislada sí pasó |
| Request → outbox → publisher periódico → SQS → Pipe → SFN → PDF | PENDIENTE: `business.py test` |
| Retry/Catch reales y recuperación de error permanente | PENDIENTE en SFN; lógica DB/contratos probados |
| Duplicado real después de SendMessage desde Lambda | PENDIENTE en infraestructura; algoritmo probado con SQL real/SQS simulado |
| Mensaje inválido termina SFN FAILED | PENDIENTE en infraestructura; normalizador aislado probado |
| IAM allow/deny efectivo | FAIL actual; preparar ENFORCE_IAM y repetir |
| Reinicio conserva DB, outbox, S3, SFN y runtime | PENDIENTE; preparar backups/mounts/persistencia antes de reiniciar |
| Karpenter crea workers Docker y ejecuta HTTP real | PENDIENTE: `karpenter.py install/start/load/test`; clúster CAPD separado |
| Borrado NodeClaim elimina worker físico y vuelve MD a cero | PENDIENTE: `karpenter.py scale-down --delete-data` sobre práctica propia |

Comandos y ejercicios: [RUNBOOK.md](RUNBOOK.md). Los tests fallan ante error real;
las suites opt-in que no se ejecutan se reportan SKIPPED, no PASS.

`aws/`, `cloudformation/`, `statemachine/`, `pom.xml` y Compose raíz permanecen
intactos. Cambios de aplicación limitados a contratos, transacciones, idempotencia,
estado, habilitación local y guardia S3. PostgreSQL de pruebas Mac es independiente
de RDS laptop, conserva datos en `.state/pg-business-tests/` y se detiene al terminar.
