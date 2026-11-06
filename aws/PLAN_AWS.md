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

- CloudFormation modular bajo `aws/`; exactamente **dos AZ** para las subredes
  y workloads administrados. No volver a preguntar por una tercera AZ.
- Entrada: cliente → API Gateway público autenticado → VPC Link → NLB interno
  → Istio → aceptación → transacción PostgreSQL de venta + outbox → respuesta 202.
- Procesamiento: outbox → Lambda publisher → SQS → EventBridge Pipe →
  Step Functions Standard → HTTP Tasks privadas → mismo NLB → Istio → micros
  → PDF en S3.
- Step Functions no llama micros mediante Lambda ni API Gateway. Mantener Pipe;
  no introducir dispatcher ni Event Bus. Identidad de workflow distinta de usuario.
- NLB interno compartido; no exponerlo públicamente. NAT, si se elige, solo para
  egreso. Ownership único del NLB, con preferencia por CloudFormation + TargetGroupBinding.
- TLS de entrada, mTLS STRICT interno, autorización efectiva, NetworkPolicy,
  secretos gestionados e IAM mínimo. No trasladar valores exclusivos del laboratorio.
- Aceptación después del commit; idempotencia durable y entrega al menos una vez.
  No prometer exactly-once de extremo a extremo. PDF no viaja en payload del workflow.
- Base On-Demand para controladores y recorrido crítico; HPA CPU/RAM y Karpenter
  con responsabilidades separadas. Distribución del recorrido crítico entre ambas AZ.
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
| D01 | ¿Entorno de pruebas, producción o ambos? | PENDIENTE | Perfil inicial y separación de entornos | — |
| D02 | ¿Presupuesto máximo mensual y moneda? | PENDIENTE | Comparar costos y tamaños | — |
| D03 | ¿Región AWS destino? | PENDIENTE; `us-east-1` es solo referencia de estimación | Compatibilidad regional y estimación | — |
| D04 | ¿Cuenta destino y perfil/rol de despliegue? | PENDIENTE; no guardar credenciales | Antes de operaciones AWS; cuenta puede derivarse con pseudoparámetros al generar YAML | — |
| D05 | ¿Dominio disponible, proveedor DNS y quién puede gestionar registros? | PENDIENTE | DNS, certificados y HTTP Tasks privadas | — |
| D06 | ¿Proveedor de identidad de usuarios, issuer y audience? | PENDIENTE | Autenticación API y permisos de usuario | — |
| D07 | ¿Identidad/credencial del workflow y responsable de su rotación? | PENDIENTE | Connection privada y autorización interna | — |
| D08 | ¿Compras promedio, pico y simultaneidad esperados? | PENDIENTE | Capacidad, concurrencia y costos | — |
| D09 | ¿Duración máxima de cada operación y latencia objetivo de aceptación/finalización? | PENDIENTE | Contratos HTTP, timeouts y escalado | — |
| D10 | ¿Tiempo máximo de recuperación (RTO) y pérdida de datos tolerable (RPO)? | PENDIENTE | Perfil RDS, backups y recuperación | — |
| D11 | ¿Desde dónde se administrará EKS: laptop, VPN, runner u otra red? | PENDIENTE | Acceso reproducible al API privado | — |

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
| E03 | Red en dos AZ: VPC, subredes, rutas, SG, endpoints/egreso | YAML y ejemplos parametrizados; lint y contratos revisados | PENDIENTE |
| E04 | ECR y bucket de artefactos; build y publicación reproducibles | Plantillas y scripts listos; publicación AWS separada | PENDIENTE |
| E05 | EKS, acceso administrativo, IAM y capacidad base | Plantillas y acceso reproducibles; versiones fijadas | PENDIENTE |
| E06 | RDS privado, secretos y S3 de documentos | Persistencia, backups, restauración y eliminación documentados | PENDIENTE |
| E07 | NLB y conectividad privada de HTTP Tasks | Recursos, DNS/TLS, identidad y ownership definidos | PENDIENTE |
| E08 | SQS/DLQ, ASL, Pipe, publisher y Scheduler | Artefactos e IAM listos; triggers inicialmente detenidos | PENDIENTE |
| E09 | Bootstrap Kubernetes, servicios y migraciones | Helm/manifiestos renderizados; TLS, políticas, HPA y Karpenter preparados | PENDIENTE |
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

Exactamente dos AZ. Mantén NLB interno, Istio, Lambda publisher, EventBridge Pipe
y Step Functions Standard con HTTP Tasks privadas. No sustituyas esa arquitectura
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
