# Laboratorio LocalStack independiente

**Host laptop con perfil `localstack-laptop`:** seguir [LAPTOP.md](LAPTOP.md).
Su gateway es `http://100.100.154.68:4566`. Las pruebas históricas de esta página
pertenecen al host anterior; no certifican la laptop nueva.

**Compras asíncronas, actualización 2026-11-04:** código implementado en `business/`
y cambios mínimos autorizados de aplicación. Usar [business/RUNBOOK.md](business/RUNBOOK.md)
para desplegar y enviar `purchase.http`. Evidencia actual: [business/VALIDATION.md](business/VALIDATION.md).
El flujo completo en Kubernetes sigue pendiente de acceso WSL; no confundirlo con
los diagnósticos históricos de esta página.

Infraestructura y herramientas exclusivas de este laboratorio. No se modifican ni
se despliegan `aws/`, `cloudformation/`, `statemachine/` ni el Compose
existente. La extensión de compras modifica aplicación/schema/tests dentro del
alcance autorizado. Cuenta local `000000000000`, región `us-east-1`, prefijo de stacks,
nombres explícitos y exports `micro-test-local-`. Los IDs asignados por servicios
no son nombres elegidos por este proyecto. CloudWatch usa su ruta convencional
`/aws/lambda/micro-test-local-diagnostic`.

**Comprobado el 3 de noviembre de 2026 desde Mac contra
`http://100.95.224.37:4566`:** LocalStack `2026.10.4:a39e30ef`, edición `pro`, licencia
activada. Diez stacks desplegados, Lambda y API Gateway ejecutados, recorrido
SQS → Pipe → Step Functions → Lambda con payload comprobado, y redrive a DLQ.
El Pipe final queda `STOPPED`. Los resultados están en [reports/](reports/).
Resumen de aceptación: [VALIDATION.md](VALIDATION.md).

**No aprobado:** IAM (una identidad sin políticas obtiene acceso), persistencia
(`disabled`), workers/pods Ready, SQL desde Kubernetes, HTTP/PDF del micro, Istio,
NetworkPolicy y push/pull ECR. No hay sesión Docker/Kubernetes de PC disponible
desde este entorno Mac. Los puertos remotos 4510 y 4511 dieron timeout; publicar
4566 no los publica. `ACTIVE`/`available` de las APIs no demuestran esos resultados.

## Estado real de la aplicación

`WorkflowController`, `WorkflowService`, `S3Storage`, `application.yml` y
`schema.sql` implementan seis pasos HTTP, seis schemas en **una misma base** y S3.
La imagen se construye con el Dockerfile existente como `docs-simulator:local`.
Spring ejecuta su propio `schema.sql`; no se aplica `aws/sql/init.sql`.

La extensión `business/` añade outbox, publisher, authorizer JWT y workflow de
compras, con activación explícita `LAB_PURCHASES_ENABLED=true`. Su despliegue requiere
bootstrap WSL. La ASL raíz anterior no se utiliza. No se añaden perfiles Spring.

```text
Disponible para pruebas de aplicación (bootstrap WSL pendiente):
API Gateway HTTP proxy → gateway Istio → un micro → PostgreSQL + S3

Fixtures ejecutados, separados de ventas:
API Gateway /diagnostic → Lambda diagnóstica
SQS diagnóstica → Pipe → Step Functions Task → Lambda diagnóstica

Objetivo de negocio pendiente:
API → aceptación → outbox → publisher → SQS → Pipe → workflow → micros
```

`fixtures/diagnostic.asl.json` contiene una tarea Lambda, no una máquina `Pass`.
La Lambda conserva `diagnosticId`, tipo y valor; no procesa ventas. El smoke
correlaciona una ejecución creada por el Pipe y excluye la ejecución directa usada
para comprobar previamente el target. Comprueba también el documento de permisos
del rol; eso **no sustituye** una prueba de IAM con enforcement.

La DLQ demuestra redrive de SQS tras relecturas. Con Standard/FIRE_AND_FORGET,
aceptar `StartExecution` no garantiza éxito posterior del workflow: sus errores
asíncronos no regresan automáticamente a esta DLQ. El workflow de negocio necesita
su propio contrato de reintentos, errores e idempotencia.

## Archivos y dependencias

| Orden | Archivo | Contenido / dependencia |
|---|---|---|
| 1 | `cloudformation/01-network.yaml` | VPC `10.77.0.0/16`, 8 subredes, 8 route tables/asociaciones, SG; sin salida por defecto |
| 2 | `02-eks.yaml` | Clúster 1.34, roles y 3 Managed Node Groups; importa red |
| 3 | `03-registry.yaml` | ECR local `micro-test-local-docs` |
| 4 | `04-database.yaml` | PostgreSQL 16, subnet/parameter groups y secreto ficticio; importa red |
| 5 | `05-storage.yaml` | S3 con versionado y bloqueo de acceso público |
| 6 | `06-queues.yaml` | SQS diagnóstica, DLQ, visibility 30 s, maxReceiveCount 3 |
| 7 | `07-lambda.yaml` | Lambda Python 3.12, rol y logs de 7 días |
| 8 | `08-workflow.yaml` | Standard Step Functions y rol que invoca solo esa Lambda |
| 9 | `09-pipe.yaml` | Rol del Pipe; importa cola y workflow. Pipe se crea por API local debido a brecha comprobada |
| 10 | `10-api.yaml` | REST API `/lab/diagnostic`, integración Lambda proxy y permiso acotado |
| 11 | `11-micro-api.yaml` | Opcional, después de WSL: HTTP proxy al gateway Istio interno; requiere parámetros generados |

EKS/ECR/RDS no son dependencias del diagnóstico serverless. Los grupos se llaman
`micro-test-local-general`, `micro-test-local-terceros`, `micro-test-local-onprem`.
Labels `lab-role=general|terceros|onprem`; terceros/onprem tienen taint
`dedicated=<rol>:NoSchedule`. Los manifiestos incluyen tolerations y pruebas de
scheduling. El micro se programa solo en `general`.

| Carpeta / herramienta | Uso |
|---|---|
| `fixtures/` | Lambda, ASL y contrato API del Pipe |
| `kubernetes/` | Namespaces, micro, PostgreSQL alternativo, gateway, mTLS, autorización, NetworkPolicy y probes |
| `parameters/` | Valores de referencia para Mac/PC; no contienen Auth Token |
| `scripts/common.py` | Guardia de endpoint, credenciales y propiedad de recursos |
| `scripts/lab.py` | Validación, cobertura, despliegue, estado, smoke y limpieza |
| `scripts/checks.py` | IAM, redrive, flujo HTTP/PDF y recuperación tras reinicio |
| `scripts/kube.py` | Bootstrap WSL, Istio, probes, ECR y parada de workloads |
| `scripts/audit.py` | Lectura de recursos reales mediante APIs de sus servicios |
| `scripts/test_safety.py` | Regresiones offline de aislamiento y fixtures |
| `.state/`, `.venv/` | Generados locales ignorados por Git; kubeconfig con modo 0600 |

Los YAML de Lambda/Step Functions contienen marcadores deliberados.
`lab.py` incorpora los fixtures y valida los YAML completos en `.state/rendered/`.
No subir los archivos fuente con marcadores directamente a CloudFormation.

## Preparar herramientas, Mac o WSL

Desde la raíz de este repositorio, Python 3.11+:

```bash
python3 -m venv localstack/.venv
localstack/.venv/bin/pip install -r localstack/requirements.txt
localstack/.venv/bin/python localstack/scripts/test_safety.py
```

El archivo de requisitos fija las versiones usadas. El SDK recibe `test/test`
explícitamente. No hace falta activar el perfil AWS `localstack`: los scripts
ignoran perfiles, archivos de configuración, tokens de sesión, roles, web identity,
metadata y proxies del entorno. Nunca usan el perfil predeterminado.

En **Mac**:

```bash
export LAB_ENDPOINT=http://100.95.224.37:4566
function lab() { localstack/.venv/bin/python localstack/scripts/lab.py --endpoint "$LAB_ENDPOINT" "$@"; }
lab probe
lab validate
lab status
```

En **PC/WSL2**, usar exactamente los mismos comandos con:

```bash
export LAB_ENDPOINT=http://127.0.0.1:4566
function lab() { localstack/.venv/bin/python localstack/scripts/lab.py --endpoint "$LAB_ENDPOINT" "$@"; }
lab probe
lab validate
```

Cada entrada operativa exige endpoint explícito, comprueba `/_localstack/health`
y `/_localstack/info`, y verifica cuenta local por STS. Solo admite HTTP en 4566
sobre `127.0.0.1`, `localhost`, `::1`, `100.95.224.37` o `100.100.154.68`. Rechaza redirects, URLs con
usuario/password, otros hosts y reescrituras del SDK fuera del gateway. Cada
cliente AWS recibe `endpoint_url`; una operación fallida termina localmente.
No hay replicator, passthrough, ECR real ni fallback a AWS.

La ampliación deliberada de transporte ocurre **dentro de WSL**: IP privada Docker
observada para DB/S3 y NodePort 31080 para gateway Istio. No amplía la lista de
endpoints AWS del SDK ni abre puertos del host. Las URLs prefirmadas se comprueban
con su host original y nunca se reescriben después de firmarlas.

## Desplegar y comprobar desde Mac o PC

```bash
# Diagnósticos y recursos base; no necesita acceso Docker desde Mac.
lab deploy --only network registry database storage queues lambda workflow pipe api
lab smoke
localstack/.venv/bin/python localstack/scripts/checks.py --endpoint "$LAB_ENDPOINT" queues

# EKS crea contenedores en la PC; reservar memoria allí.
lab deploy --only eks
localstack/.venv/bin/python localstack/scripts/audit.py --endpoint "$LAB_ENDPOINT"
lab status
```

Cada stack lleva tag `Lab=micro-test-local`. Un stack existente sin ese tag se
rechaza. Repetir `deploy` sobre la misma definición comprueba igualdad y no recrea
datos. Un cambio de plantilla existente se detiene para revisión de un change set;
no se hacen reemplazos automáticos de una base o un clúster. Tampoco se adopta un
recurso manual existente. Errores/rollback se muestran con eventos de CloudFormation.

**Cobertura real de esta instalación:**

| Observación | Tratamiento local |
|---|---|
| `DescribeType` responde `TypeNotFoundException` para tipos incorporados | No inferir ausencia de servicio; desplegar y leer con API correspondiente. `coverage.json` conserva resultado bruto |
| CloudFormation creó Pipe RUNNING pese a STOPPED | `09-pipe.yaml` administra solo IAM. `fixtures/pipe.json` + `CreatePipe(DesiredState=STOPPED)` administran Pipe explícitamente |
| CreatePipe STOPPED permaneció CREATING | Reafirmar STOPPED con `UpdatePipe`, esperar estado efectivo antes de terminar bootstrap; no arrancarlo |
| `StopPipe` no implementado en esta instalación | Transiciones con `UpdatePipe(RUNNING/STOPPED)`, verificadas; no se presupone paridad del ciclo de vida |
| DeletePipe detenido quedó DELETING | Limpieza conservadora mantiene Pipe detenido y dependencias. Borrado completo requiere `--delete-data`; activa brevemente antes de eliminar |
| `LoggingConfig.LogGroup` no dirigió logs al grupo personalizado | Usar grupo convencional `/aws/lambda/micro-test-local-diagnostic`, ahora administrado por CloudFormation |
| RDS `DescribeEvents` no implementado | Inspeccionar `DescribeDBInstances`, eventos CFN y SQL desde WSL |
| EKS/nodegroups ACTIVE, RDS available | Metadatos comprobados; runtime Kubernetes/SQL sigue pendiente |

Las páginas de cobertura actuales pueden diferir de la imagen instalada. No se
interpreta un 200 de validación, un schema de registro o CREATE_COMPLETE como
prueba de ejecución. No se ha certificado update/rollback de todos los recursos.

## Bootstrap de PC/WSL2

Requiere Docker Linux de Docker Desktop, `kubectl`, `k3d` e `istioctl 1.31.3`.
No instalar otro Docker Engine. El script fija socket Docker local y genera
kubeconfig independiente desde **el clúster k3d de este laboratorio**; rechaza
credenciales exec, endpoints no locales y TLS sin verificación. No usa el contexto
Kubernetes actual. Istio 1.31 admite Kubernetes 1.32–1.37; se comprueba versión real,
no solo el `Version: 1.34` de EKS.

Instalar el binario fijado si todavía no existe, dentro de archivos locales:

```bash
mkdir -p localstack/.state/tools
curl -fL https://github.com/istio/istio/releases/download/1.31.3/istio-1.31.3-linux-amd64.tar.gz \
  -o localstack/.state/tools/istio.tar.gz
tar -xzf localstack/.state/tools/istio.tar.gz -C localstack/.state/tools
export PATH="$PWD/localstack/.state/tools/istio-1.31.3/bin:$PATH"
istioctl version --remote=false
k3d cluster list
docker ps --format '{{.Names}} {{.Image}}'
```

Seleccionar nombres **observados** en esas listas; no inventar el sufijo generado:

```bash
read -rp 'Clúster k3d micro-test-local-eks...: ' LAB_K3D_CLUSTER
read -rp 'Contenedor LocalStack existente: ' LAB_LOCALSTACK_CONTAINER
read -rp 'Red Docker de ese contenedor (ej. micro-test-lab): ' LAB_DOCKER_NETWORK

localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint http://127.0.0.1:4566 --k3d-cluster "$LAB_K3D_CLUSTER" bootstrap \
  --localstack-container "$LAB_LOCALSTACK_CONTAINER" --docker-network "$LAB_DOCKER_NETWORK" \
  --database rds --presign-endpoint http://100.95.224.37:4566
```

Ese comando verifica workers Ready/labels/taints, instala Istio mínimo, conecta
solo contenedores del clúster a la red existente, construye/importa la imagen,
ejecuta SQL desde un Job, despliega el micro y gateway, comprueba scheduling de
los tres grupos y ejecuta pruebas de seguridad. Si falla, termina sin declarar
PASS. No se ha ejecutado desde esta Mac.

La DB del stack usa credenciales **ficticias y públicas de laboratorio**
`docs/local-docs-only`, también representadas en Secrets Manager y Secret de
Kubernetes. No sirven para un entorno compartido o producción. Para probar SQL
TLS en RDS emulado, modificar explícitamente el JDBC/cliente de prueba después de
comprobar compatibilidad; la conexión funcional base no afirma verificar TLS.

Si RDS no llega a ejecutar SQL, alternativa **explícita**:

```bash
localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint http://127.0.0.1:4566 --k3d-cluster "$LAB_K3D_CLUSTER" bootstrap \
  --localstack-container "$LAB_LOCALSTACK_CONTAINER" --docker-network "$LAB_DOCKER_NETWORK" \
  --database postgres --presign-endpoint http://100.95.224.37:4566
```

Usa un StatefulSet PostgreSQL 16 con PVC en el mismo clúster. Es otra base vacía,
no una migración ni prueba de RDS. No cambiar de backend a mitad de una venta.
No se borra el PVC al parar workloads; borrar EKS/k3d puede perder su almacenamiento.
Importar la imagen con k3d no demuestra pull ECR ni autorización del kubelet.

### mTLS, autorización y red

Bootstrap comprueba en este orden: cliente sin sidecar funciona con PERMISSIVE;
STRICT lo rechaza; cliente con sidecar funciona; otra service account recibe 403;
un cliente con identidad autorizada pierde conectividad al cambiar su label de
red y la recupera al restaurarlo. Conserva STRICT y políticas incluso si falla
un diagnóstico. Son cambios temporales solo en namespace de este laboratorio;
no ejecutar estas pruebas sobre ventas en curso.

NetworkPolicy limita **ingress al micro**, no todo egress del clúster. Flannel
transporta paquetes; el controlador embebido kube-router de K3s aplica las reglas.
El script comprueba servidor K3s sin `--disable-network-policy` y exige las pruebas
allow/deny. Sin controlador identificado, se detiene. No se atribuye enforcement
a Flannel por sí solo. Tampoco instala Cilium sobre un CNI existente.

```bash
localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint http://127.0.0.1:4566 --k3d-cluster "$LAB_K3D_CLUSTER" ready
localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint http://127.0.0.1:4566 --k3d-cluster "$LAB_K3D_CLUSTER" security
```

## HTTP real del micro y PDF

Después de bootstrap, en **WSL** crear API opcional con dirección Docker observada:

```bash
lab deploy --only micro-api --parameters localstack/.state/micro-api-parameters.json
```

`11-micro-api.yaml` envía HTTP desde LocalStack al NodePort 31080 del gateway
Istio en la red Docker. No publica 31080 en Windows ni representa ALB, PrivateLink
o VPC Link. Gateway → micro usa mTLS; API Gateway → gateway es HTTP local.
El authorizer de negocio continúa pendiente; estas APIs de laboratorio no exigen
autenticación de clientes.

Desde **Mac o WSL**, obtener ID sin utilizar AWS CLI ni endpoints externos:

```bash
LAB_MICRO_API_ID=$(localstack/.venv/bin/python - "$LAB_ENDPOINT" <<'PY'
import sys
sys.path.insert(0, 'localstack/scripts')
from common import Lab
print(Lab(sys.argv[1]).outputs('micro-api')['ApiId'])
PY
)
export LAB_MICRO_URL="$LAB_ENDPOINT/_aws/execute-api/$LAB_MICRO_API_ID/lab"
localstack/.venv/bin/python localstack/scripts/checks.py \
  --endpoint "$LAB_ENDPOINT" micro --micro-url "$LAB_MICRO_URL"
```

La prueba registra venta con referencia única, verifica idempotencia, prueba
rechazo de completar póliza antes de subir documento, recorre seis pasos y
comprueba `sales/<saleId>/<documentId>.pdf`, MIME, `%PDF-` y descarga prefirmada
idéntica. Guarda IDs/hash, nunca URL firmada, en `.state/http-proof.json`.
Liberar documento no cambia automáticamente venta a COMPLETED.

Alternativa HTTP inicial solo en WSL, sin API opcional:

```bash
kubectl --kubeconfig "$PWD/localstack/.state/kubeconfig" -n micro-test-local-general \
  port-forward --address 127.0.0.1 service/micro-test-local-gateway 8080:8080
# Otra terminal WSL, manteniendo el port-forward:
localstack/.venv/bin/python localstack/scripts/checks.py \
  --endpoint http://127.0.0.1:4566 micro --micro-url http://127.0.0.1:8080
```

Presign endpoint debe ser alcanzable por quien ejecuta la prueba. Elegir
`http://127.0.0.1:4566` al hacer bootstrap si la descarga solo se hará en PC.
No reemplazar hostname de una URL ya firmada.

## ECR, IAM y persistencia

En WSL, después de construir imagen y tener kubeconfig local:

```bash
localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint http://127.0.0.1:4566 --k3d-cluster "$LAB_K3D_CLUSTER" registry
```

Comprueba URI ECR devuelta, mapea deliberadamente transporte a `127.0.0.1:4566`,
hace login con token obtenido del **ECR emulado**, push y pull por digest. Config
Docker temporal bajo `.state/`, eliminada al salir. No modifica daemon.json,
registros inseguros globales ni credenciales Docker del usuario. Si el registry
no admite ese transporte, falla y queda pendiente; no intenta ECR real.

IAM usa una identidad temporal creada por el IAM local comprobado; jamás acepta
claves externas por argumento ni las guarda. Las claves generadas por esta versión
pueden empezar con AKIA; su procedencia local, no el prefijo textual, es la garantía.
El test borra usuario/políticas/clave al terminar, incluso al fallar.

```bash
localstack/.venv/bin/python localstack/scripts/checks.py --endpoint "$LAB_ENDPOINT" iam
```

**Estado actual: FAIL.** Para aprobar, preparar en PC una sesión con
`ENFORCE_IAM=1`, guardando antes datos/configuración y siguiendo el mecanismo de
arranque existente. No hay comando que cambie o reinicie automáticamente ese
Compose. Repetir prueba: denegación sin políticas, Allow al objeto exacto,
denegación de escritura no concedida y Deny explícito. Bootstrap/app con `test/test`
son administrativos; no representan identidades restringidas de pods ni Pod Identity.

**Persistencia actual: disabled.** No reiniciar para probar recuperación sin
preparar `PERSISTENCE=1` y volumen durable de LocalStack en PC. El cambio requiere
recrear su contenedor con configuración del operador; no conserva automáticamente
estado que ya estaba solo en memoria. Esta entrega no toca su Compose ni hace ese
reinicio destructivo. Con persistencia preparada y el primer smoke HTTP guardado:

```bash
# Solo WSL; después de guardar datos, confirmar volumen durable y PERSISTENCE=1.
# Usar exclusivamente el nombre observado de este contenedor de laboratorio.
docker restart "$LAB_LOCALSTACK_CONTAINER"
lab status
localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint http://127.0.0.1:4566 --k3d-cluster "$LAB_K3D_CLUSTER" ready
localstack/.venv/bin/python localstack/scripts/checks.py \
  --endpoint "$LAB_ENDPOINT" restore --micro-url "$LAB_MICRO_URL"
lab smoke
```

`restore` lee y verifica el PDF original **antes** de llamar endpoints capaces de
regenerarlo; exige mismos IDs y SHA-256. Ejecutarlo desde el equipo donde existe
`.state/http-proof.json` y con URLs alcanzables. Repetir además después de reiniciar
WSL/Docker/Windows; sobrevivir al reinicio de un contenedor no prueba todo ese ciclo.
No reutilizar reportes PASS de una sesión anterior como evidencia de la nueva.

## Limpieza aislada y conservación

Primero parar workloads en **WSL**:

```bash
localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint http://127.0.0.1:4566 --k3d-cluster "$LAB_K3D_CLUSTER" stop
```

Desde Mac o PC:

```bash
lab cleanup
lab status
```

Limpieza conservadora elimina las APIs y deja Pipe STOPPED. Mantiene EKS/PVC,
RDS/secreto, ECR/imágenes, S3/versiones, SQS/DLQ y red. Debido a la brecha de
DeletePipe detenido, conserva también stack IAM del Pipe, workflow y Lambda/logs:
son dependencias del Pipe retenido. No inicia consumidores para intentar borrar
recursos con datos. No equivale a retirar todos los contenedores ni liberar RAM
de EKS; `stop` retira workloads de prueba.

**Borrado irreversible de todos los datos de este laboratorio:**

```bash
lab cleanup --delete-data
lab status
```

Activa brevemente el Pipe detenido para sortear su bug de borrado; mensajes
diagnósticos pendientes pueden consumirse. Elimina Pipe por API, luego stacks en
orden inverso: APIs, IAM Pipe, workflow, Lambda/logs, colas, bucket/versiones,
RDS/secreto, imágenes/repositorio, EKS/nodos y red. Vacia solo bucket/repositorio
propiedad de estos stacks. No crea snapshot final de RDS; su eliminación está
reservada a esta opción explícita. Un error detiene limpieza y conserva dependencias
restantes para inspección. No hay `docker system prune`, reset global de LocalStack
ni eliminación de volúmenes ajenos.

La ruta destructiva completa no se ejecutó: se dejaron los recursos desplegados
para continuar desde PC. Se comprobó eliminación de dos Pipes exclusivamente
diagnósticos durante investigación de compatibilidad.

## Límites y exclusiones

- SG, subredes, AZ y route tables son modelos API: no prueban firewall/routing Docker.
  La API devolvió además una regla autorreferenciada en el SG de DB tras crear RDS;
  el audit la conserva. No usar esa metadata como certificado de aislamiento.
- No EC2 administrativa, NAT/IGW, VPC endpoints, ALB, KMS ni addons adicionales:
  no aportan una prueba funcional necesaria en este alcance. No afirmar PrivateLink
  ni condiciones `aws:SourceVpce` usando una red Docker.
- Kubeconfig k3d usa certificados administrativos. No demuestra EKS Access Entries,
  IRSA, Pod Identity, IAM del kubelet, EC2 real o disponibilidad entre AZ físicas.
- PostgreSQL emulado no prueba seguridad de RDS: `rds.force_ssl` se acepta pero no
  obliga TLS; `verify-ca`/`verify-full` no están soportados y `pg_stat_ssl` puede
  reportar false aun con conexión cifrada. Pruebas TLS negativas quedan excluidas
  como evidencia de paridad; SQL real sigue siendo obligatorio para funcionalidad.
- RDS 16, AZ fijas y password ficticio provocan warnings conocidos de cfn-lint
  W3691/W3010/W2501. No hay errores de schema/referencias. Esas elecciones son
  exclusivamente locales; no son parámetros sugeridos para desplegar AWS real.
- PVC local-path y nodos de k3d comparten la PC. Guardar backups SQL/PDF fuera del
  clúster antes de un borrado intencional; esta entrega no inventa HA o snapshots.

Fuentes primarias consultadas, además de las pruebas contra la imagen instalada:
[CloudFormation](https://docs.localstack.cloud/aws/services/cloudformation/),
[EKS](https://docs.localstack.cloud/aws/services/eks/),
[Pipes](https://docs.localstack.cloud/aws/services/pipes/),
[RDS/TLS](https://docs.localstack.cloud/aws/services/rds/),
[IAM enforcement](https://docs.localstack.cloud/aws/developer-tools/security-testing/iam-policy-enforcement/),
[compatibilidad Istio](https://istio.io/latest/docs/releases/supported-releases/),
[controlador NetworkPolicy K3s](https://docs.k3s.io/networking/networking-services).
