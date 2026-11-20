# Plan de implementación AWS — documento vivo

Última actualización: **2026-11-05**.
Estado: **definición de entorno; CloudFormation AWS pendientes de generación**.

Este archivo conserva el prompt base y el seguimiento de las decisiones,
respuestas y entregables. Se actualizará conforme avancemos; no es necesario
responder todo ni crear toda la infraestructura en una sola sesión.

El usuario ejecutará los comandos contra AWS. Generar archivos y validarlos
offline no autoriza desplegar, publicar artefactos, crear change sets ni modificar
la cuenta. No hacer push ni desplegar sin autorización explícita.
Esta actualización solo documenta el plan; no inicia la implementación.

## 1. Cómo mantener este documento

1. Registrar cada respuesta en la tabla de decisiones, con fecha y alcance.
2. Distinguir decisiones confirmadas de propuestas y datos pendientes. No
   convertir valores de ejemplo en valores aprobados ni volver a preguntar
   decisiones ya resueltas.
3. Antes de implementar una etapa, resolver solo sus decisiones bloqueantes.
   Parametrizar los valores que puedan definirse al desplegar.
4. Al crear una plantilla, registrar ruta real, dependencias, parámetros y Outputs.
   Mantener el orden ejecutable en [CLOUDFORMATION.md](CLOUDFORMATION.md).
5. Al validar, anotar comando, resultado, fecha y evidencia. Separar validación
   offline, consulta AWS, change set, despliegue y prueba funcional.
6. Tras cada avance, actualizar estado, pendientes, siguiente acción e historial.
   Si cambia una decisión, conservar el motivo y qué decisión reemplaza.
7. Guardar referencias a secretos, nunca contraseñas, tokens, claves privadas
   ni URLs firmadas. Revisar reportes antes de versionarlos.

Estados de trabajo: `PENDIENTE`, `EN CURSO`, `COMPLETO` o `BLOQUEADO: motivo`.
Una etapa completa de código puede seguir con validación AWS pendiente.

## 2. Punto de partida y documentos relacionados

| Elemento | Estado conocido |
|---|---|
| Plantillas CloudFormation para AWS real | No existen todavía bajo `aws/` |
| Plantillas LocalStack | Nueve; exclusivas del laboratorio, no desplegables en AWS como están |
| Validación local de plantillas, 2026-11-05 | Cero errores, diez warnings; detalle en la guía CloudFormation |
| Consultas/validación/despliegue AWS de esta tarea | No ejecutados |
| Respuestas de configuración del entorno | Pendientes; no se ha seleccionado región/cuenta destino |

- [README AWS](README.md): entrada general.
- [Arquitectura](ARCHITECTURE.md): diseño y análisis; sus alternativas no son
  decisiones aprobadas si contradicen los requisitos vigentes.
- [CloudFormation](CLOUDFORMATION.md): validación y ejecución manual por etapas.
- [LocalStack](../localstack/README.md) y [evidencia local](../localstack/VALIDATION.md):
  contratos reutilizables y pruebas todavía pendientes. No prueban paridad AWS.

No recuperar diseños retirados del historial como infraestructura aprobada.
Conservar funcionando el laboratorio durante el trabajo AWS.

## 3. Requisitos ya confirmados

> **Revisión 2026-11-19.** Las decisiones N01–N09 (sección 4) reemplazan en este
> documento: EventBridge Pipe → Lambda consumidora; Lambda publisher → worker
> outbox en EKS; RDS → Aurora PostgreSQL; HPA → KEDA. El anexo conserva el
> prompt original sin reescribir.

- CloudFormation modular bajo `aws/`; exactamente **dos AZ** para las subredes
  y workloads administrados. No volver a preguntar por una tercera AZ.
- Entrada: cliente → API Gateway público → VPC Link → NLB interno
  → Istio → aceptación → transacción Aurora PostgreSQL de venta + outbox → 202.
- Procesamiento: outbox → worker publisher (EKS) → SQS (+DLQ) → Lambda
  consumidora → Step Functions Standard → HTTP Tasks privadas → mismo NLB →
  Istio → API → documento en S3. Paso 1 → 2 → 3, tres reintentos por paso.
- Step Functions no llama micros mediante Lambda ni API Gateway. La Lambda solo
  inicia la ejecución; la SFN cierra el mensaje SQS (patrón lease, N02).
  Identidad de workflow distinta de usuario.
- NLB interno compartido; no exponerlo públicamente. NAT, si se elige, solo para
  egreso. Ownership único del NLB, con preferencia por CloudFormation + TargetGroupBinding.
- TLS de entrada, mTLS STRICT interno, autorización efectiva, NetworkPolicy,
  secretos gestionados e IAM mínimo. No trasladar valores exclusivos del laboratorio.
- Aceptación después del commit; idempotencia durable y entrega al menos una vez.
  No prometer exactly-once de extremo a extremo. PDF no viaja en payload del workflow.
- Base On-Demand para controladores y recorrido crítico; KEDA (pods) y Karpenter
  (nodos) con responsabilidades separadas. Distribución del recorrido crítico entre ambas AZ.
- Build, publicación, bootstrap Kubernetes y migraciones son pasos explícitos,
  separados de CloudFormation.
- Objetivos: costo controlado, aceptación rápida, procesamiento asíncrono confiable
  y despliegue reproducible. Presupuesto y SLO numéricos aún pendientes.

El prompt completo del anexo conserva los requisitos detallados. Una alternativa
descrita allí como evaluación no queda elegida automáticamente.

## 4. Respuestas y decisiones del usuario

Completar la columna de respuesta con cada conversación. `PENDIENTE` significa
que todavía no hay respuesta; no implica aceptar un valor por defecto.

| ID | Pregunta / decisión | Respuesta confirmada | Cuándo se necesita | Fecha |
|---|---|---|---|---|
| D01 | ¿Entorno de pruebas, producción o ambos? | Laboratorio semi-productivo: simular fallos/caídas, detener, escalar y medir límites | Perfil inicial y separación de entornos | 2026-11-19 |
| D02 | ¿Presupuesto máximo mensual y moneda? | Sin cifra: lo menos costoso posible aprovisionando todos los componentes | Comparar costos y tamaños | 2026-11-19 |
| D03 | ¿Región AWS destino? | `us-east-1` | Compatibilidad regional y estimación | 2026-11-19 |
| D04 | ¿Cuenta destino y perfil/rol de despliegue? | PENDIENTE; no guardar credenciales | Antes de operaciones AWS; cuenta puede derivarse con pseudoparámetros al generar YAML | — |
| D05 | ¿Dominio disponible, proveedor DNS y quién puede gestionar registros? | `mercally.dev` registrado en Hostinger (sin CAA); su DNS completo pasa a Route 53 (N16) | DNS, certificados y HTTP Tasks privadas | 2026-11-19 |
| D06 | ¿Proveedor de identidad de usuarios, issuer y audience? | Sin autenticación por ahora; si resulta obligatoria, Lambda authorizer en API Gateway. Mitigar con throttling de stage | Autenticación API y permisos de usuario | 2026-11-19 |
| D07 | ¿Identidad/credencial del workflow y responsable de su rotación? | PENDIENTE | Connection privada y autorización interna | — |
| D08 | ¿Compras promedio, pico y simultaneidad esperados? | Sin cifra: arrancar mínimo y cargar SQS/outbox progresivamente hasta encontrar el límite | Capacidad, concurrencia y costos | 2026-11-19 |
| D09 | ¿Duración máxima de cada operación y latencia objetivo de aceptación/finalización? | PENDIENTE | Contratos HTTP, timeouts y escalado | — |
| D10 | ¿Tiempo máximo de recuperación (RTO) y pérdida de datos tolerable (RPO)? | PENDIENTE | Perfil RDS, backups y recuperación | — |
| D11 | ¿Desde dónde se administrará EKS: laptop, VPN, runner u otra red? | PENDIENTE | Acceso reproducible al API privado | — |

### Decisiones 2026-11-19 (reemplazan diseño anterior donde se indica)

| ID | Decisión | Reemplaza |
|---|---|---|
| N01 | Lambda consumidora de SQS (ESM, batch 1, `MaximumConcurrency` como control de admisión) inicia Step Functions | EventBridge Pipe |
| N02 | Patrón lease: Lambda devuelve `batchItemFailures` tras `StartExecution` (el ESM no borra); ejecución nombrada `messageId-receiveCount`; SFN hace `sqs:deleteMessage` al éxito y `changeMessageVisibility` en Catch; visibility timeout > timeout SFN; `maxReceiveCount` → DLQ. Hasta 3 reintentos/paso × N entregas | DLQ solo por fallo de Lambda |
| N03 | Aurora PostgreSQL Serverless v2, mínimo 0 ACU (auto-pausa) | RDS PostgreSQL Multi-AZ |
| N04 | KEDA escala pods (SQS/outbox); Karpenter escala nodos | HPA CPU/RAM |
| N05 | mTLS STRICT con CA de istiod. KMS solo cifrado en reposo (Aurora, S3, SQS, secrets EKS). Sin ACM Private CA | KMS para mTLS |
| N06 | Outbox publisher = worker Spring en la misma imagen, Deployment aparte activado por variable de entorno | Lambda publisher |
| N07 | TLS público: certificado ACM en listener TLS del NLB interno con dominio del usuario; NLB → Istio gateway | Certificado gestionado en Istio |
| N08 | Costo mínimo: un NAT (no tolera caída de AZ), nodos Graviton, Karpenter Spot, sin VPC interface endpoints (NAT más barato), S3 gateway endpoint | — |
| N09 | API pública sin autenticación inicial; rutas `/internal/*` nunca expuestas en API Gateway | JWT authorizer obligatorio |
| N10 | Subredes en dos AZ (obligatorio para EKS y RDS, sin costo); workloads (nodos, Karpenter, Aurora writer, NLB) solo en AZ A al inicio para evitar transferencia inter-AZ. Ampliar a AZ B por parámetro | Workloads distribuidos en dos AZ |
| N11 | ECR ya existe en la cuenta del usuario: sin stack registry; imagen como parámetro. Lambda consumidora inline en plantilla, sin bucket de artefactos | Stack ECR + bucket artefactos |
| N13 | Flujo de 3 pasos idempotentes según diagrama del usuario: (1) documento → S3 temporal, (2) subida a CM, (3) emisión con Aseguradora con acuse. Cada paso consulta estado, reconcilia con el externo y solo entonces actúa. 3 intentos por paso; liberar mensaje para reintento global; 3 entregas → DLQ. Borrado del archivo temporal tras `DeleteMessage`. Código: `ProcessService`, `/api/v1/internal/purchases/{id}/steps/*`, `aws/stepfunctions/purchase.asl.json` | Modelo claim/owner (bug: reentrega borraba mensaje sin procesar) |
| N14 | CM y Aseguradora simulados por `ExternalMocksController` (misma imagen, `MOCKS_ENABLED`), fallos raros configurables: caída y acuse perdido | — |
| N15 | DLQ: log + alarma CloudWatch (más económico). SNS a correo posterior | Lambda sobre DLQ |
| N16 | DNS (revisada 2026-11-19): Hostinger no permite NS de subdominio, así que el DNS de `mercally.dev` completo pasa a Route 53 (nameservers cambiados en Hostinger, registros previos A apex y `www` copiados) y `00-dns.yaml` delega dentro de Route 53 la zona del lab `aws.mercally.dev`. `mercally.com` intacto. Host interno `api-internal.aws.mercally.dev`, certificado ACM con validación DNS automática. Guía: `aws/DNS.md` | Delegar `aws.mercally.com` (Hostinger sin NS de subdominio); comprar dominio nuevo |
| N17 | Secreto propio de Aurora (`micro-test/aurora/master`) **sin rotación**, cifrado con KMS; los pods lo leen al arrancar | Secreto gestionado por RDS con rotación cada 7 días; ESO y AWS JDBC Wrapper descartados |
| N18 | TLS termina en el NLB (listener 443, ACM); NLB → Istio gateway HTTP 8080, health 15021; solo VPC Link y Resource Gateway (por SG) llegan al NLB y solo el NLB al gateway | Regla 8443 abierta a toda la VPC |
| N19 | CloudFront sirve Angular (S3 privado, OAC, SSE-S3) y enruta `/api/*` al HTTP API: mismo origen, sin CORS. Frontend acepta base `/api/v1` y JWT opcional fuera de LocalStack | CloudFront y API en orígenes separados con CORS |
| N20 | Bootstrap con `aws/kubernetes/bootstrap.sh` idempotente: valores desde Outputs CFN, secretos desde AWS por stdin, kubeconfig aislado. Controladores en nodos base; api/worker/mocks solo en NodePool Karpenter `workloads` (arm64, Spot/On-Demand, tope CPU). KEDA escala solo api (SQS + CPU); worker y mocks fijos (un scaler sobre Aurora impediría su auto-pausa). `/internal/*` exige `X-Workflow-Key` (AuthorizationPolicy DENY) — cierra D07. Istio sin reintentos en el VirtualService | HPA CPU/RAM; NetworkPolicy (pendiente) |
| N21 | Observabilidad: add-on `amazon-cloudwatch-observability` (Container Insights, Fluent Bit, Application Signals) con Pod Identity y log groups con retención (05); agente ADOT inyectado por anotación, sin agente en la imagen; dashboard `micro-test-lab` y activación Application Signals (08). Guía: `aws/diagrams/OBSERVABILIDAD.md` | Agente OTel embebido sin collector |
| N22 | Sincronización (corrige N01/N02): la Lambda espera su ejecución (timeout 330 s), así `MaxConcurrency` = ejecuciones activas. `API_MAX_REPLICAS` = ceil(N/8) derivado en bootstrap; sin trigger CPU en KEDA; apagado ordenado de Spring. Guía: `aws/diagrams/ESCALADO.md` | Lambda que solo arrancaba la ejecución (ESM no limitaba ejecuciones); KEDA independiente de la admisión |
| N23 | BD: RDS PostgreSQL 16 single-AZ `db.t4g.micro` (gp3 20 GiB, KMS, secreto propio) reemplaza Aurora Serverless v2 (2026-11-20). La app no usa nada propio de Aurora; con pods siempre conectados Aurora nunca se pausa (≥0.5 ACU ≈ $44/mes) frente a ~$14/mes. Perilla `DbInstanceClass`. Se pierde Data API y escalado automático. El Free plan bloqueó Aurora y no soporta VPC Lattice: cuenta pasa a plan pagado | Aurora Serverless v2; Aurora express configuration (Free plan) |
| N24 | Registro: el usuario no tenía ECR; `09-registry.yaml` crea `micro-test/app` (tags mutables, scan on push, conserva 10 imágenes, `EmptyOnDelete`). Tag de imagen = commit corto + fecha | Repositorio creado a mano |
| N25 | UI de seguimiento: `GET /api/v1/purchases?limit=` (ruta en 06) devuelve últimas ventas con outbox e historial; `process.history` registra cada intento de paso (RUNNING → resultado) fuera de la transacción del paso; `process.lab_settings` define una espera aleatoria por paso (default 0–10 s, máx 15 s) leída en cada llamada. UI con auto-refresco y árbol por venta | Lista solo en `localStorage`; latencia fija por variable de entorno |
| N26 | Panel de pruebas: UI sin configuración LocalStack (mismo origen `/api/v1`). API `/api/v1/lab/*` (ruta 06 `ANY /api/v1/lab/{proxy+}`): tiempos, vista del sistema (SQS principal + DLQ, outbox, pods/HPA/nodos vía RBAC de solo lectura), ráfaga al outbox (máx. 1000, una transacción), redrive de DLQ (`StartMessageMoveTask`). Fallos inducidos 1–20 intentos. Rol `api` (05) con SQS GetQueueAttributes + move task; 04 exporta ARN/URL de DLQ. Público como el resto (N09) | Panel en herramienta aparte; métricas solo en CloudWatch |
| N12 | NLB interno se mantiene: puerta estable para VPC Link y Resource Gateway, y soporte del certificado ACM. Istio balancea L7 dentro del cluster | — |

Pruebas AWS pendientes derivadas: verificar si el ESM reduce concurrencia ante
`batchItemFailures`. Bug de re-claim resuelto por N13 (`ProcessIntegrationTest` 7/7, 2026-11-19).

Cuenta, nombres, dominios y tamaños se parametrizarán donde corresponda. No hacen
falta claves AWS para escribir YAML o ejecutar lint. La falta de un valor que se
resolverá al desplegar no debe bloquear trabajo local independiente.

### Decisiones técnicas a preparar con esas respuestas

| ID | Análisis pendiente | Resultado / decisión | Estado |
|---|---|---|---|
| T01 | NAT frente a endpoints según egreso real; costo y comportamiento ante caída de AZ | PENDIENTE | PENDIENTE |
| T02 | RDS Multi-AZ primario/standby o perfil económico Single-AZ; tamaño, backups y necesidad de Proxy | PENDIENTE | PENDIENTE |
| T03 | Tipo de API, identidad de usuario/workflow, DNS y ciclo completo del certificado TLS en Istio | PENDIENTE | PENDIENTE |
| T04 | Compatibilidad regional/versiones de EKS, CNI, LBC, Istio, metrics-server y Karpenter | PENDIENTE | PENDIENTE |
| T05 | Ownership del NLB, cross-zone, acceso privado HTTP Task y reglas de red | PENDIENTE | PENDIENTE |
| T06 | Workloads Karpenter, perfiles On-Demand/Spot, límites y recuperación ante pérdida de AZ | PENDIENTE | PENDIENTE |
| T07 | IAM por componente, retención, alarmas, trazas y políticas de eliminación | PENDIENTE | PENDIENTE |
| T08 | Fronteras de stacks, contratos Parameters/Outputs y grafo sin ciclos | PENDIENTE | PENDIENTE |

Consultar documentación oficial vigente al resolver cada análisis y registrar
fuentes, fecha, supuestos y efecto en costo/disponibilidad. Presentar las opciones
que cambien materialmente seguridad, costo o disponibilidad antes de elegirlas.

## 5. Etapas de implementación

Orden inicial propuesto. No es una lista de archivos existentes ni un script de
despliegue. Ajustar dependencias al definir contratos entre stacks.

| Etapa | Trabajo / entregable | Criterio para cerrar | Estado |
|---|---|---|---|
| E00 | Registrar contexto, prompt y guía de validación | Documentos enlazados y límites explícitos | COMPLETO |
| E01 | Resolver decisiones iniciales y estimar costo mensual por componente | Decisiones registradas; supuestos, fuentes y pendientes visibles | PENDIENTE |
| E02 | Definir módulos, parámetros, Outputs y orden | Grafo sin ciclos; ninguna dependencia futura requerida | PENDIENTE |
| E03 | Red en dos AZ: VPC, subredes, rutas, SG, endpoints/egreso | YAML y ejemplos parametrizados; lint y contratos revisados | EN CURSO: 01-network.yaml listo, lint OK |
| E04 | ECR y bucket de artefactos; build y publicación reproducibles | Plantillas y scripts listos; publicación AWS separada | Sin stack (N11); build/push a ECR existente PENDIENTE |
| E05 | EKS, acceso administrativo, IAM y capacidad base | Plantillas y acceso reproducibles; versiones fijadas | EN CURSO: 05-eks listo (lint OK). Acceso: endpoint público por `AdminCidr` (D11) |
| E06 | Aurora privado, secretos y S3 de documentos | Persistencia, backups, restauración y eliminación documentados | EN CURSO: 02-security y 03-data listos, lint OK |
| E07 | NLB y conectividad privada de HTTP Tasks | Recursos, DNS/TLS, identidad y ownership definidos | EN CURSO: 06-ingress listo (lint OK); conectividad Step Functions → Resource Gateway por verificar en AWS |
| E08 | SQS/DLQ, ASL, Lambda consumidora, worker outbox | Artefactos e IAM listos; triggers inicialmente detenidos | EN CURSO: 04-messaging listo (lint + test Lambda); validación ASL en AWS PENDIENTE |
| E09 | Bootstrap Kubernetes, servicios y migraciones | Helm/manifiestos renderizados; TLS, políticas, HPA y Karpenter preparados | EN CURSO: `aws/kubernetes/` (bootstrap.sh, platform.yaml, app.yaml) renderiza y parsea offline; ejecución en EKS PENDIENTE. Versiones de charts por verificar |
| E10 | API Gateway autenticado y VPC Link | Contratos públicos y autorizaciones definidos | PENDIENTE |
| E11 | Consolidar validación offline y manual de operación | Lint, ASL, renderizados y pruebas relevantes; comandos exactos en la guía | PENDIENTE |
| E12 | Usuario valida/revisa change sets y despliega AWS por etapas | Dependencias reales disponibles, resultados registrados | PENDIENTE |
| E13 | Pruebas AWS, activación, operación y recuperación | Evidencia de compra/PDF, seguridad, resiliencia, escalado y restauración | PENDIENTE |

La validación offline acompaña cada etapa; E11 consolida resultados. Las etapas
AWS no se marcan completas por haber generado archivos o aprobado lint.

### Registro de plantillas creadas

Por ahora no hay entradas. Agregar una fila por plantilla real; no completar con
nombres de archivos ficticios. El orden de ejecución se mantendrá sincronizado con
[CLOUDFORMATION.md](CLOUDFORMATION.md).

| Orden | Archivo real | Stack / entorno | Depende de | Parámetros de entrada | Outputs consumidos | Validación offline | Validación/despliegue AWS |
|---|---|---|---|---|---|---|---|
| 00 | `aws/cloudformation/00-dns.yaml` | `micro-test-dns` | — (luego nameservers en Hostinger, `aws/DNS.md`) | `ParentZoneName` = `mercally.dev`, `ZoneName` = `aws.mercally.dev` (defaults) | — (exporta HostedZoneId, ZoneName; output NameServers) | cfn-lint 2026-11-19: 0 errores | PENDIENTE |
| 01 | `aws/cloudformation/01-network.yaml` | `micro-test-network` | — | `Project`, `ClusterName`, `VpcCidr` (10.80.0.0/16) | — (exporta VpcId, VpcCidr, Public/App/DbSubnetIds, App/DbSecurityGroupId) | cfn-lint 2026-11-19: 0 errores | PENDIENTE |
| 04 | `aws/cloudformation/04-messaging.yaml` | `micro-test-messaging` | 02 (06 ingress para `ResourceConfigurationArn`) | `ApiBaseUrl`, `WorkflowApiKey` (NoEcho), `ResourceConfigurationArn` (vacío hasta 06), `MaxReceiveCount` 3, `ReleaseDelaySeconds` 30, `MaxConcurrency` 2, `ConsumerEnabled` | DataKeyArn (exporta QueueUrl/Arn/Name) | cfn-lint 2026-11-19: 0 errores; `aws/tests/test_consumer.py` 3/3 | PENDIENTE |
| 05 | `aws/cloudformation/05-eks.yaml` | `micro-test-eks` | 01, 02, 03, 04 | `AdminCidr` (obligatorio), `KubernetesVersion` 1.36 (verificar), `NodeInstanceType` t4g.medium, `NodeCount` 2, `AppNamespace` micro | AppSubnetIds, AppSecurityGroupId, DataKeyArn, DocumentsBucketArn, DbSecretArn, QueueArn (exporta ClusterName, ClusterSecurityGroupId) | cfn-lint 2026-11-19: 0 errores | PENDIENTE |
| 06 | `aws/cloudformation/06-ingress.yaml` | `micro-test-ingress` | 00 (delegación verificada), 01 | `ApiHostLabel` api-internal, `CorsAllowOrigin` *, throttling 10/20 | HostedZoneId, ZoneName, VpcId, AppSubnetIds, AppSecurityGroupId (exporta IstioTargetGroupArn; outputs ApiBaseUrl, ResourceConfigurationArn para 04) | cfn-lint 2026-11-19: 0 errores | PENDIENTE |
| 07 | `aws/cloudformation/07-frontend.yaml` | `micro-test-frontend` | 06 | `IngressStack` | HttpApiId | cfn-lint 2026-11-19: 0 errores; `npm run build` OK | PENDIENTE |
| 02 | `aws/cloudformation/02-security.yaml` | `micro-test-security` | — | `Project` | — (exporta DataKeyArn) | cfn-lint 2026-11-19: 0 errores | PENDIENTE |
| 03 | `aws/cloudformation/03-data.yaml` | `micro-test-data` | 01, 02 | `EngineVersion` (16.6, verificar), `MaxCapacity` (4 ACU), `SecondsUntilAutoPause`, `DocumentRetentionDays` | DbSubnetIds, DbSecurityGroupId, DataKeyArn | cfn-lint 2026-11-19: 0 errores | PENDIENTE |

### Registro de evidencia AWS

Por ahora no hay entradas. Guardar resultados sin secretos y distinguir pruebas
ejecutadas por el usuario de resultados revisados directamente.

| Fecha | Etapa / stack | Cuenta/entorno y región | Operación o prueba | Resultado | Evidencia | Pendiente / próxima acción |
|---|---|---|---|---|---|---|

## 6. Próximo paso concreto

Recoger primero estas respuestas:

1. Uso: pruebas, producción o ambos; presupuesto máximo mensual.
2. Región destino.
3. Dominio/DNS disponible y proveedor de identidad de usuarios.
4. Carga esperada, duración de operaciones y tolerancia a interrupción/pérdida de datos.

Registrar las respuestas en D01–D10. Con ellas, preparar decisiones técnicas y
estimación, identificar lo que falte para la primera etapa y comenzar módulos
locales cuando sus dependencias de diseño estén resueltas. No pedir nuevamente dos AZ.

## 7. Prompt para retomar en otra sesión

```text
Continúa la preparación del ambiente AWS de este repositorio usando
aws/PLAN_AWS.md como documento vivo de decisiones y avance.

Lee AGENTS.md, aws/README.md, aws/PLAN_AWS.md, aws/ARCHITECTURE.md y
aws/CLOUDFORMATION.md. Inspecciona el estado real del repositorio antes de editar.
Consulta localstack/VALIDATION.md para separar evidencia local de validación AWS.

Respeta los requisitos confirmados y el prompt base conservado en el anexo.
No vuelvas a preguntar decisiones ya registradas. Si aporto nuevas respuestas,
actualiza sus filas y fecha; no inventes respuestas que falten. Para cada etapa,
resuelve solo las decisiones bloqueantes y avanza el trabajo independiente.

Implementa progresivamente los entregables locales que solicitemos: plantillas
CloudFormation, parámetros, artefactos, scripts, Helm/manifiestos y pruebas.
Registra rutas reales, dependencias, Parameters/Outputs y resultados. Mantén
aws/CLOUDFORMATION.md con el orden exacto y los comandos que ejecutaré en AWS.

Exactamente dos AZ. Mantén NLB interno, Istio, worker outbox publisher, SQS+DLQ,
Lambda consumidora con patrón lease (N02), Aurora PostgreSQL, KEDA, Karpenter y
Step Functions Standard con HTTP Tasks privadas. No sustituyas esa arquitectura
por diseños anteriores ni copies valores exclusivos de LocalStack.

Separar generación, lint offline, validación AWS, change sets, despliegue y pruebas
funcionales. Marcar como PENDIENTE todo lo no ejecutado. Yo ejecutaré contra AWS;
generar código no autoriza publicar artefactos, crear change sets, desplegar ni
modificar cuentas. No hacer push ni despliegues sin autorización explícita.

Al terminar cada avance, actualiza decisiones, etapas, evidencia, historial y
próxima acción en aws/PLAN_AWS.md. Resume qué cambió y qué falta.
```

## 8. Historial de avance

| Fecha | Avance / decisión | Evidencia / pendiente |
|---|---|---|
| 2026-11-05 | Inspección: solo existen CloudFormation locales; AWS pendiente de generación | README y guía CloudFormation |
| 2026-11-05 | Lint de nueve plantillas locales: cero errores y diez warnings | Detalle y comandos en CLOUDFORMATION.md; no se ejecutó AWS |
| 2026-11-05 | Guía de validación/change sets/ejecución manual; requisito de dos AZ reflejado en documentación | Orden AWS todavía propuesto |
| 2026-11-05 | Creación de este documento vivo a petición del usuario | Próximo paso: registrar respuestas iniciales |
| 2026-11-19 | Respuestas D01–D03, D05, D06, D08; decisiones N01–N09 (Lambda consumidora, lease SQS, Aurora, KEDA, worker outbox, istiod mTLS) | Inicio stack 01 network |
| 2026-11-19 | N13–N15: pasos idempotentes con reconciliación, mocks CM/Aseguradora, worker outbox Spring, ASL AWS, DLQ → CloudWatch | `mvn test` con PostgreSQL efímero: Process 7/7, Purchase 9/9; ASL sin validar en AWS |
| 2026-11-19 | Validación Kubernetes con kind (`aws/kubernetes/validate.sh`) | 21 objetos aceptados en dry-run servidor (K8s 1.37) con CRDs de LBC 1.14.0, Karpenter 1.8.1, Istio 1.28.0, KEDA 2.18.0; prueba negativa rechazada; flags Helm verificados. Compatibilidad con EKS 1.35 sigue por verificar |
| 2026-11-19 | N21–N22: observabilidad CloudWatch, Lambda que espera su ejecución, réplicas derivadas de MaxConcurrency | cfn-lint 0 errores; test_consumer 4/4; dashboard JSON válido |
| 2026-11-19 | Pruebas del worker outbox (`OutboxPublisherIntegrationTest`) | 5/5 con PostgreSQL efímero y SQS falso; mutación sin `SKIP LOCKED` detectada (`duplicate send`) |

## Anexo — prompt base original

Se conserva el texto original de la solicitud de infraestructura para evitar
perder requisitos. Su inclusión documenta el objetivo; no ejecuta acciones ni
amplía por sí misma la autorización de despliegue. Las respuestas posteriores
se registran arriba, sin reescribir silenciosamente este texto.

```text
Genera en este repositorio la infraestructura AWS real de nuestra arquitectura,
usando CloudFormation y exactamente DOS zonas de disponibilidad para las
subredes y workloads que administramos.

Objetivo: implementación reproducible, costo controlado, aceptación rápida
y procesamiento asíncrono confiable. Generar código y validarlo offline
NO autoriza desplegar, crear recursos facturables ni modificar cuentas AWS.

CONTEXTO

Lee primero:
- AGENTS.md
- aws/README.md
- aws/ARCHITECTURE.md
- localstack/README.md y localstack/VALIDATION.md
- localstack/business/
- localstack/kubernetes/
- localstack/scaling/
- Código de aplicación y Dockerfile.

Inspecciona implementación actual antes de diseñar cambios. Reutiliza contratos,
lógica de negocio y pruebas útiles. No copies directamente infraestructura local
ni recuperes diseños retirados del historial como arquitectura aprobada.

Los resultados LocalStack no prueban permisos, disponibilidad, TLS, costos
ni conectividad privada de AWS. Conserva funcionando laboratorio local.

ARQUITECTURA REQUERIDA

Entrada:
Cliente → API Gateway público autenticado → VPC Link → NLB interno →
Istio Gateway → micro de aceptación → transacción PostgreSQL venta + outbox.

Procesamiento:
Outbox → Lambda publisher → SQS → EventBridge Pipe →
Step Functions Standard → HTTP Tasks por conectividad privada compatible →
mismo NLB interno → Istio → micros → PDF en S3.

Condiciones:
- Step Functions NO invoca Lambdas ni pasa por API Gateway para llamar micros.
- Lambda publisher permanece; authorizer Lambda solo si identidad lo requiere.
- Mantener EventBridge Pipes. No introducir dispatcher ni Event Bus.
- NLB balancea conexiones; Istio selecciona servicio por host/ruta.
- No exponer NLB públicamente ni usar NAT como acceso entrante.
- NAT, si se necesita, sirve exclusivamente para egreso.
- No trasladar IPs Docker, NodePort 31080, puerto 4566, credenciales test/test,
  JWT ficticios ni certificados locales.

DECISIONES ANTES DE IMPLEMENTAR

Presenta arquitectura concreta y preguntas realmente bloqueantes:
región, cuenta destino, presupuesto, dominio/DNS, identidad de usuarios,
identidad del workflow, volumen esperado, duración de pasos y RTO/RPO.

Dos AZ ya están decididas: no vuelvas a preguntarlo.
Usa us-east-1 como referencia de estimación, no como destino autorizado.
Parametriza valores de entorno; no inventes cuentas, dominios, ARN ni secretos.

Consulta documentación oficial vigente para disponibilidad regional,
compatibilidad de versiones, recursos CloudFormation y precios. Señala
incompatibilidades antes de cambiar arquitectura. No elijas silenciosamente
opciones que alteren seguridad, disponibilidad o costos importantes.

IMPLEMENTACIÓN

1. Red y dos AZ
- VPC, direccionamiento sin solapamientos, subred privada de aplicaciones y
  subred aislada de base de datos en cada AZ.
- Subredes públicas solamente si estrategia de egreso las requiere.
- Rutas, security groups, DNS y endpoints necesarios.
- Compara costos de NAT y VPC endpoints con dependencias reales.
- No presentes NAT único como egreso tolerante a caída de una AZ.
- Acceso administrativo al API EKS explícito y reproducible.

2. EKS, Istio y NLB
- Capacidad base On-Demand en ambas AZ para controladores y recorrido de aceptación.
- Karpenter no debe depender para arrancar de nodos que él mismo debe crear.
- AWS VPC CNI, metrics-server, AWS Load Balancer Controller, Istio y Karpenter
  con versiones compatibles y fijadas.
- Un NLB interno compartido, targets IP de pods Istio Gateway.
- Define ownership único: preferentemente CloudFormation crea NLB/listeners/
  target groups y AWS LBC reconcilia pods mediante TargetGroupBinding.
  No permitas que un Service cree un segundo NLB accidentalmente.
- Decide cross-zone explícitamente; documenta disponibilidad y transferencia.
- TLS de entrada, mTLS STRICT gateway→micro y micro→micro, autorización por
  identidad y NetworkPolicy realmente aplicada por el componente elegido.
- Resuelve emisión, distribución y renovación del certificado en Istio.
  Un ARN ACM por sí solo no crea un Secret TLS Kubernetes.

3. HTTP Tasks privadas
- Implementa EventBridge Connection privada, Resource Configuration y
  Resource Gateway mediante mecanismos oficiales compatibles.
- No confundas endpoint privado de APIs Step Functions con salida del workflow.
- Verifica HTTPS, DNS, certificado de confianza pública, hostname/SNI y
  restricciones actuales de HTTP Tasks.
- No supongas soporte de mTLS cliente en HTTP Tasks.
- Considera límite de 60 segundos por llamada: operaciones largas requieren
  contrato asíncrono y consulta de estado; un 202 no significa trabajo terminado.
- Identidad del workflow distinta de identidad del usuario. Rutas internas
  requieren autorización efectiva, no solo nombres difíciles de adivinar.

4. Datos y negocio
- PostgreSQL RDS privado, secretos gestionados, cifrado, backups y restauración.
- Para alta disponibilidad en dos AZ, evaluar instancia RDS Multi-AZ
  primario/standby; no elegir modalidad que exija tres AZ.
- Single-AZ solo como perfil económico explícito con limitaciones documentadas.
- RDS Proxy únicamente si patrón de conexiones/costo lo justifica.
- Aceptación devuelve 202 después del commit transaccional venta+outbox.
- Idempotencia durable, leases, recuperación del publisher y entrega al menos
  una vez; no prometer exactly-once de extremo a extremo.
- S3 privado, acceso mínimo, URLs firmadas y ciclo de vida.
- PDF no viaja como payload de Step Functions.

5. Mensajería y workflow
- SQS/DLQ, retención, visibility timeout y redrive justificados.
- Pipe y permisos para iniciar Standard.
- Publisher en VPC con acceso DB, SQS y secretos; programación y concurrencia
  acotadas según capacidad.
- Retry/backoff/jitter, timeouts y recuperación explícita de errores permanentes.
- Evitar multiplicación de reintentos entre Istio, aplicación y workflow.
- Distinguir error de entrega/inicio de fallo posterior de ejecución.
- Pipes no ofrece un límite configurable de N ejecuciones por segundo:
  no inventes parámetros de rate limiting.

6. Escalado y resiliencia
- HPA CPU y RAM con requests/limits medidos, considerando recursos de sidecars.
- Mínimo dos réplicas del recorrido crítico distribuidas entre AZ.
- Karpenter NodePools/EC2NodeClasses, límites, diversificación de instancias,
  interrupciones Spot, budgets y consolidación.
- Define qué aplicaciones realmente ejecutará Karpenter; no heredes
  automáticamente limitación local de practicar solo con nginx.
- Topology spread, PDB, readiness, drenaje Istio y deregistration NLB coordinados.
- Documenta cómo recupera capacidad si falla una AZ.
- HPA escala pods; Karpenter aporta nodos. Ninguno controla admisión de compras.

7. Seguridad y observabilidad
- IAM mínimo por componente mediante identidad de workload compatible.
- Separar permisos de despliegue y ejecución.
- Secretos fuera de Git, parámetros visibles, outputs y logs.
- Deshabilitar mecanismos locales de inyección de fallos en perfil productivo.
- Logs, métricas, alarmas y retención controlada.
- Correlación saleId/eventId/executionArn y propagación de contexto por outbox,
  SQS y llamadas HTTP.
- Instrumentación OpenTelemetry real cuando corresponda; no presentar historial
  reconstruido de Step Functions como trazas internas de todos los servicios.

ENTREGABLES

- CloudFormation modular bajo aws/, parámetros de ejemplo sin secretos,
  outputs y dependencias claras, sin referencias circulares.
- ASL, artefactos Lambda, IAM, Helm/manifiestos y configuración necesarios.
- Cambios mínimos de aplicación cuando AWS real los requiera.
- Scripts reproducibles para build, publicación, migraciones, bootstrap,
  validación, actualización y eliminación.
- aws/README.md como entrada única: requisitos, orden exacto, comandos,
  operación, rollback y recuperación.
- Explica separación entre CloudFormation y bootstrap Kubernetes; no escondas
  instalación de Helm o migraciones detrás de recursos inexistentes.
- Política de retención/eliminación para datos, secretos y backups.
- Estimación mensual por componente, con supuestos y fuentes; separar costo fijo,
  tráfico, NAT/endpoints, logs y transferencia entre AZ.
- Diagrama y decisiones relevantes actualizados.
- Commits coherentes. No hagas push ni despliegues sin autorización explícita.

VALIDACIÓN

Ejecuta lint CloudFormation, validación ASL y renderizado Helm/manifiestos,
más pruebas relevantes de aplicación e integración de contratos.

Prepara pruebas AWS para:
- Compra .http autenticada → 202 → COMPLETED → PDF.
- Idempotencia, duplicados, caída del publisher después de enviar.
- Timeout después de commit, errores transitorios/permanentes y recuperación.
- Autorización negativa, aislamiento, TLS y mTLS.
- HTTP Tasks privadas al NLB sin Lambda de transporte.
- HPA CPU/RAM, nodos reales Karpenter, consolidación y pérdida de nodo/AZ.
- Persistencia, restauración y medición de aceptación frente a finalización.

Marca pruebas no ejecutadas como PENDIENTES. No equipares lint,
CREATE_COMPLETE o controller Ready con arquitectura completamente validada.

Primero entrega inspección y decisiones bloqueantes. Una vez resueltas,
implementa todos los entregables autorizados; no te quedes solo en propuesta.
```
