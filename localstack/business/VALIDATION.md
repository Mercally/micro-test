# Laptop: evidencia y pendientes — 2026-11-04

Destino único: `http://100.100.154.68:4566`, cuenta `000000000000`, `us-east-1`.
LocalStack observado: `2026.10.4:a39e30ef`, Pro, licencia activa, Docker Desktop
sobre WSL2 x86_64. No se consultó host anterior ni AWS real.

**Resultado actual: flujo de compras completo PASS en laptop.**
[Suite real, nueve comprobaciones PASS](../reports/laptop/20261105T015536Z-fb6d94/test.json):
auth válida/ausente/inválida/expirada, aceptación/outbox, publicación periódica,
SQS/Pipe/SFN, PDF y descarga, idempotencia, errores transitorios, fallo permanente
recuperable, duplicado después de envío y evento inválido.
Micro/ECR/Istio fueron desplegados por usuario en WSL; pruebas API se ejecutaron
desde Mac exclusivamente contra laptop. **Aceptación total de arquitectura sigue
incompleta: IAM efectivo FAIL; persistencia tras reinicio y Karpenter pendientes.**
Guía directa: [PRACTICE.md](PRACTICE.md).
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
| Kubernetes real en WSL | PASS de inspección aportada por usuario | Servidor v1.34.9+k3s1, cuatro nodos Ready, labels/taints esperados y TLS verificado. No prueba scheduling de pods. [resumen de salida](../reports/laptop/20261104T220247Z-kube-513657/user-inspection-summary.json) |
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

Continuación WSL: salida aportada por usuario confirma CLI k3d v5.8.3, kubectl
disponible y `istioctl` ausente del PATH. Clúster generado se llama
`micro-test-local-50dacdf7`, con tres contenedores agent y un server running.
Esto **no demuestra Kubernetes Ready, taints efectivos ni versión real**; versión
k3s mostrada por `k3d version` es predeterminada del CLI. No se archiva salida
Docker completa porque contiene tokens. Se corrigió filtro incorrecto `*-eks*`:
selección por red observada. La primera corrección agregó una comparación CA
contra EKS que resultó incorrecta y detuvo inspección sin modificar Kubernetes. Se agregó
`wsl.py --inspect`, sin despliegue ni requisito Istio, y chequeo temprano de herramientas.
Siete tests de regresión nuevos, ocho guardias y tres contratos existentes pasaron.
En ese punto, inspección Kubernetes en laptop seguía pendiente.

Corrección posterior de TLS: consulta real de `DescribeCluster` confirmó que EKS
devuelve certificado con subject `CN=localhost.localstack.cloud`, issuer Google
Trust Services WR1; corresponde al endpoint gateway `https://localhost.localstack.cloud:4511`.
No debe igualarse a CA interna k3d. Consulta EC2 confirmó tres instance IDs de
workers observados y tags `eks:cluster-name=micro-test-local-eks` y propiedad
`kubernetes.io/cluster/micro-test-local-eks=owned`.
Se reemplazó igualdad CA por correlación red/labels Docker/puerto API/instancias
EC2 propias. Kubeconfig conserva CA interna y validación TLS obligatoria. Diez
tests WSL (incluyendo certificados distintos y rechazo de cluster ajeno), ocho
guardias y tres contratos pasaron. Esas pruebas offline no prueban conectividad.

Inspección posterior ejecutada por usuario, sesión `20261104T220247Z-kube-513657`:
TLS y asociación Docker/EC2 funcionaron; servidor real `v1.34.9+k3s1`, cuatro nodos
Ready. Workers general/onprem/terceros tienen labels esperados; onprem/terceros
tienen sus taints `dedicated=<rol>:NoSchedule`; control plane también tiene
NoSchedule. `istioctl` sigue ausente. Cliente kubectl `v1.36.1` difiere dos minors:
usar cliente local 1.34.x antes del bootstrap, sin actualizar servidor. Persistencia
sigue desactivada. Falta probar pods, Istio, NetworkPolicy y flujo de compra.

Bootstrap posterior del usuario, sesión `20261104T222141Z-a06b2e`: terminó con
`docker failed: error: no such object: dd219f68d2eb` al resolver NodePort. Por ubicación
del traceback, código ya había retornado de instalación Istio, build/import, SQL,
rollouts, scheduling y `security()`. Es evidencia de avance, pero no se recibieron
reportes completos de esos pasos para archivarlos como PASS independiente.
Causa del fallo: lista de IDs Docker obtenida antes de build/import se reutilizaba
al final. No se conoce por qué desapareció ese contenedor concreto. Se cambió
resolución final para consultar nodos Ready y contenedores actuales, filtrando roles
server/agent; ante desaparición durante lectura, vuelve a consultar inventario hasta tres
intentos, sin ocultar otros errores Docker. Seis tests de regresión nuevos más
dieciocho existentes pasaron; reejecución WSL y flujo completo siguen pendientes.

Reejecución aportada por usuario, sesión `20261104T230151Z-kube-ee803c`:
bootstrap reportó **PASS** con Kubernetes 1.34.9+k3s1, Istio 1.31.3 y SQL real a
`docs`/`docs`, PostgreSQL 16.15. Resolución NodePort usó worker onprem en
`172.19.0.7`. Ese resultado se emite después de rollouts, scheduling y pruebas
de seguridad; evidencia detallada `mesh.json` permanece en laptop. Flujo se detuvo
después, en registry, al hacer login contra `127.0.0.1:4566/v2/` (404).

Consulta real ECR devolvió repositorio y autorización bajo
`000000000000.dkr.ecr.us-east-1.localhost.localstack.cloud:4566`. GET HTTPS `/v2/`
conservando hostname, conectado desde Mac al gateway laptop, respondió 200 y
`Docker-Distribution-Api-Version: registry/2.0`, con TLS verificado. Se corrigió
reescritura de host y se habilitó comando registry independiente de bootstrap.
Siete pruebas ECR nuevas y 24 existentes pasaron; no prueban login/push/pull Docker.
Actualización posterior: salida WSL aportada por usuario confirma push/pull ECR por
`sha256:221772b88a5013a585899ef66fe8f9db1efc01c7a61eb88413bf0d4beea56d86`.
Reporte original permanece en laptop: `20261104T231233Z-ecr-a09c2a/ecr.json`.
Stack de negocio llegó a CREATE_COMPLETE; después falló UpdatePipe por omitir RoleArn.
Se corrigió argumento y dos pruebas con modelo botocore pasaron. Llamada real desde
Mac confirmó [Pipe STOPPED](../reports/laptop/20261104T231617Z-a89e6d/pipe-stop.json).

Siguiente bloqueo ejecutado y confirmado: [health publisher/transport FAIL](../reports/laptop/20261104T231553Z-3bb4aa/runtime-health.json).
Publisher agota timeout hacia `172.19.0.5:4566`; transport hacia
`172.19.0.7:31080`. Inventario WSL posterior confirmó redes separadas: publisher/transport están
solo en `bridge` (172.17.*), mientras endpoints configurados apuntan a k3d
(172.19.*). LocalStack está conectado a ambas redes. `network_inspect.py` invoca solo health y muestra
redes actuales sin secretos. Tres pruebas de filtrado pasaron; usuario ejecutó inspección Docker real.
[Evidencia recibida](../reports/laptop/20261105T004708Z-a24666/user-network-inspection.json). No se activaron Pipe ni regla periódica.
Compra completa todavía NO APROBADA.

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
- Stack `micro-test-local-business` **UPDATE_COMPLETE**; red reparada. Se añadió `micro-test-local-business-scheduler`, con rol limitado al publisher. Flujo real PASS.
- Generado `.state/business/purchase.http` con API real y tokens de una hora, ignorado por Git.

Código nuevo incluye aceptación/outbox, publisher, authorizer, transporte, ASL,
CloudFormation, despliegue WSL, generador `.http`, suite E2E y comparación de
persistencia. Sintaxis/contratos probados no sustituyen las pruebas pendientes.

## Estado de aceptación actual

| Prueba | Estado / cómo ejecutarla |
|---|---|
| Workers Ready, labels y taints | PASS de inspección WSL aportada por usuario |
| Pods Ready y scheduling en tres grupos | PASS según bootstrap WSL aportado por usuario |
| Micro contra RDS y rollout | PASS: bootstrap WSL, health Lambda y compra completa después de reparación |
| Push/pull ECR por digest | PASS según salida WSL aportada por usuario; digest indicado arriba |
| Istio mTLS positivo/negativo y NetworkPolicy con controles | PASS según bootstrap WSL agregado; `mesh.json` detallado permanece en laptop |
| API JWT válida/ausente/firma incorrecta/expirada sobre API Gateway | PASS: API real, tres rechazos 401 y compra autenticada 202 |
| Request → outbox → publisher periódico → SQS → Pipe → SFN → PDF | PASS: suite real enlazada arriba |
| Retry/Catch reales y recuperación de error permanente | PASS: dos TransientStepError; FAILED permanente; recuperación COMPLETED con nueva generación |
| Duplicado real después de SendMessage desde Lambda | PASS: crash real, outbox pendiente, dos ejecuciones SUCCEEDED para mismo eventId y documento lógico único |
| Mensaje inválido termina SFN FAILED | PASS: evento versión 99 terminó ejecución FAILED / InvalidEvent |
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

## Reparación preparada: red Lambda existente — 2026-11-05 UTC

`wsl.py --repair-lambda-network --network k3d-micro-test-local-50dacdf7` valida
propiedad EKS/k3d, conserva DB y conecta un nodo Ready a la red Lambda existente
si hace falta. Usa `--gw-priority -1` para conservar la ruta predeterminada del nodo.
Actualiza endpoints de negocio mediante CloudFormation y ejecuta health, dejando
Pipe/regla detenidos. No ejecuta bootstrap, build, ECR ni reinicia emulador.

Siete pruebas de reparación, diez WSL, dos Pipe y seis descubrimiento: **25 PASS**.
Esto prueba lógica y guardias con Docker simulado; cambio Docker y conectividad
reparada siguen **PENDIENTES en laptop**. No es aprobación E2E.

Consulta API actual confirmó stack CREATE_COMPLETE, Pipe STOPPED y regla DISABLED:
[estado previo](../reports/laptop/20261105T005121Z-f6d6e7/before-network-repair.json).
Persistencia sigue deshabilitada. Nuevo authorizer puede arrancar posteriormente;
su ausencia en inventario de contenedores no demuestra ausencia de función Lambda.

## Evidencia final de compras — 2026-11-04, hora de El Salvador

Reportes usan timestamp UTC (2026-11-05). Reparación WSL confirmada por usuario:
[resumen](../reports/laptop/20261105T014204Z-kube-675850/user-repair-summary.json).
NodePort `172.17.0.3:31080`, AWS/RDS `172.17.0.2:4566/4510`, misma base docs.

Defectos ejecutados antes de corregir:
- EnableRule devolvió ENABLED sin publicación: [timeout real](../reports/laptop/20261105T014412Z-b3a9ae/test.json).
- Después de refrescar regla/destino sí publicó; DisableRule no detuvo publicación:
  [pausa FAIL](../reports/laptop/20261105T015207Z-453035/schedule-pause.json).
- Test de historial SFN falló al serializar datetime de boto3, aunque venta
  transitoria completó: [reporte parcial](../reports/laptop/20261105T014931Z-b88b53/test.json).

Se desacopló destino de regla heredada y se creó stack separado de Scheduler.
Control `UpdateSchedule` conserva campos opcionales; código rechaza recursos ajenos.
No se modificaron funciones Java, colas, ASL ni PDFs para resolver esos fallos.
Cuatro pruebas de control/clock, siete de reparación, diez WSL y dos Pipe pasaron;
cuatro templates aprobaron cfn-lint. Cinco tokens recién emitidos según reloj del
emulador respondieron 200 consecutivamente. Esto no sustituye prueba de IAM.

Suite final completa: [test.json](../reports/laptop/20261105T015536Z-fb6d94/test.json).
Compra principal `7b1fb745-4bc2-4f7a-98a8-d815e1e5bf3f` terminó COMPLETED,
SFN SUCCEEDED y PDF S3 con SHA256
`a52ce5c1e73a54d173aef8fb8a67bf382858b09355928ef2c8853ba204670d18`.
Caída publisher dejó dos ejecuciones SUCCEEDED para evento
`3830d19f-bb10-41d1-89f4-e666e3d42d8e`, attempts=2 y mismo documento lógico.

Se guardó referencia para futura prueba de persistencia:
[snapshot previo](../reports/laptop/20261105T015750Z-1bc742/snapshot.json).
No hubo reinicio; no se aprueba persistencia. Datos de práctica quedan conservados.

Pausa/reanudación nueva ejecutada: [test-pause PASS](../reports/laptop/20261105T015956Z-a9565a/test-pause.json).
Venta `4cfb3fcf-1af8-42f3-94ae-404dafe47370` permaneció ACCEPTED/outbox sin
published_at durante 75 segundos; al reanudar llegó automáticamente a COMPLETED,
SFN SUCCEEDED y PDF descargable. No se invocó publisher manualmente en esta prueba.

[Estado final](../reports/laptop/20261105T020226Z-0d63c9/final-runtime.json):
Scheduler ENABLED, Pipe RUNNING, regla anterior sin targets. Listo para `.http`.
