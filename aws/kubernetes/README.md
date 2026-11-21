# Bootstrap Kubernetes y operación del laboratorio

Paso posterior a CloudFormation 00–07 ([runbook](../cloudformation/README.md)). Instala
controladores con Helm y aplica los manifiestos de este directorio. Todo valor sale de
los Outputs de CloudFormation; los secretos se leen de AWS al ejecutar y no tocan disco.

| Archivo | Contenido |
|---|---|
| [bootstrap.sh](bootstrap.sh) | Instalación idempotente: LBC, Karpenter, Istio, KEDA, plataforma, secretos y app |
| [platform.yaml](platform.yaml) | EC2NodeClass + NodePool `workloads`, mTLS STRICT, Gateway, TargetGroupBinding |
| [app.yaml](app.yaml) | Namespace `micro`, Deployments `api`/`worker`/`mocks`, VirtualService, AuthorizationPolicies, ScaledObject |
| [validate.sh](validate.sh) | Validación offline con kind: versiones fijadas existen, dry-run en servidor contra los CRDs reales, flags de Helm aplicados |

## Qué corre dónde

| Nodos | Workloads |
|---|---|
| Base (2× t4g.medium On-Demand, `role=base`, CloudFormation) | coredns, metrics-server, LBC, Karpenter, istiod, Istio gateway, KEDA |
| `workloads` (Karpenter, Spot/On-Demand arm64, AZ A, tope `KARPENTER_CPU_LIMIT`) | api (KEDA 1–N), worker (1), mocks (1) |

Ningún pod de la app cabe en los nodos base: el primer despliegue ya hace que Karpenter
lance un nodo. Ese crecimiento es lo que se observa en el laboratorio.

## 0. Validar antes de tocar AWS

```bash
aws/kubernetes/validate.sh
```

Termina en `VALIDATION OK`. Ejecutarlo tras cambiar manifiestos o versiones de charts.

## 1. Imagen arm64 en tu ECR

Los nodos son Graviton: la imagen debe ser `linux/arm64` (nativa en Mac Apple Silicon).
El repositorio lo crea el stack 09 (una vez):

```bash
aws cloudformation deploy --stack-name micro-test-registry \
  --template-file aws/cloudformation/09-registry.yaml
```

Build y push (cada vez que cambie el código). El tag lleva commit + fecha para distinguir
builds con cambios sin commitear:

```bash
REPO_URI=$(aws cloudformation describe-stacks --stack-name micro-test-registry \
  --query "Stacks[0].Outputs[?OutputKey=='RepositoryUri'].OutputValue" --output text)
export IMAGE=$REPO_URI:$(git rev-parse --short HEAD)-$(date +%Y%m%d%H%M)
echo "$IMAGE"

aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "${IMAGE%%/*}"
docker build --platform linux/arm64 -t "$IMAGE" .
docker push "$IMAGE"
```

El node role ya tiene `AmazonEC2ContainerRegistryReadOnly`: no hace falta configurar pull secrets.

## 2. Bootstrap

Requisitos: helm, kubectl, aws CLI, python3; stacks 00–07 desplegados; tu IP dentro de
`AdminCidr` (05). Antes, revisar que las versiones fijadas al inicio de `bootstrap.sh`
soporten la versión de Kubernetes del cluster.

```bash
IMAGE=$IMAGE aws/kubernetes/bootstrap.sh
```

Tarda ~10–15 min (incluye el primer nodo de Karpenter y el pull de la imagen). Termina
mostrando nodos, TargetGroupBinding, salud de targets del NLB y pods. Usa un kubeconfig
temporal; para operar después:

```bash
aws eks update-kubeconfig --name micro-test --alias micro-test-aws
```

Para cambiar imagen o perillas: volver a ejecutar con las variables nuevas.

## 3. Activar el consumo y probar

```bash
aws cloudformation deploy --stack-name micro-test-messaging \
  --template-file aws/cloudformation/04-messaging.yaml --capabilities CAPABILITY_IAM \
  --parameter-overrides ConsumerEnabled=true

SITE=$(aws cloudformation describe-stacks --stack-name micro-test-frontend \
  --query "Stacks[0].Outputs[?OutputKey=='SiteUrl'].OutputValue" --output text)
SALE=$(curl -s -X POST "$SITE/api/v1/purchases" -H 'Content-Type: application/json' \
  -d '{"externalReference":"AWS-001","customerName":"Lab","customerEmail":"lab@example.com","productCode":"AUTO-01"}' |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["saleId"])')
sleep 20; curl -s "$SITE/api/v1/purchases/$SALE" | python3 -m json.tool
```

También desde la UI: `$SITE` con URL base `/api/v1`.

## Perillas del laboratorio

| Perilla | Dónde | Efecto |
|---|---|---|
| `MaxConcurrency` | 04-messaging | Ejecuciones simultáneas que entran a Step Functions |
| `ConsumerEnabled` | 04-messaging | `false`: la cola acumula; `true`: drena |
| `ThrottlingRateLimit` / `ThrottlingBurstLimit` | 06-ingress | Tasa máxima de compras por la API pública |
| `DbInstanceClass` | 03-data | Tamaño de RDS PostgreSQL (reinicio ~5 min al cambiar) |
| `API_MAX_REPLICAS`, `API_MESSAGES_PER_POD` | bootstrap.sh | Pods de api y mensajes SQS por pod antes de escalar |
| `KARPENTER_CPU_LIMIT` | bootstrap.sh | vCPU máximas que Karpenter puede lanzar |
| `API_CPU_REQUEST` | bootstrap.sh | CPU reservada por pod api (default 250m); subirla junto con `step_cpu_ms` para que KEDA + Karpenter agreguen CPU real |
| `document_min_kb`, `document_max_kb` | tabla `process.lab_settings` / panel | Tamaño real del documento firmado (default 200–500 KB) |
| `step_cpu_ms`, `step_memory_mb` | tabla `process.lab_settings` / panel | CPU real y memoria por paso de negocio (simulación de trabajo) |
| `step_delay_min_ms`, `step_delay_max_ms` | tabla `process.lab_settings` (SQL, en vivo) | Espera aleatoria antes de cada paso de negocio; default 0–10000 ms ([CARGA.md](../CARGA.md)) |
| `MOCKS_FAILURE_PROBABILITY`, `MOCKS_LATENCY_MS` | bootstrap.sh | Fallos raros y lentitud de CM/Aseguradora |

## Simular fallos

```bash
curl -s -X POST "$SITE/api/v1/purchases" -H 'Content-Type: application/json' -H 'X-Lab-Failure: cm:transient:2' \
  -d '{"externalReference":"AWS-F1","customerName":"Lab","customerEmail":"lab@example.com","productCode":"AUTO-01"}'
kubectl -n micro delete pod -l app=api
kubectl -n micro scale deploy/mocks --replicas=0
kubectl delete node -l karpenter.sh/nodepool=workloads
```

Pasos válidos para `X-Lab-Failure`: `document`, `cm`, `insurer`.

## Carga y escalado

Nodos de workloads: Karpenter elige entre `small`, `medium` y `large` (t4g/c7g/m7g, arm64). Con
pocos pods lanza `t4g.small` (~2 pods api por nodo), así cada paso de KEDA hace que Karpenter
agregue un nodo; con un lote grande de pods pendientes puede elegir uno mayor. t4g es burstable:
CPU sostenida consume créditos (modo unlimited, cobro extra pequeño).

Istio reparte la carga hacia api con `DestinationRule` (`app.yaml`): `LEAST_REQUEST` (pod con
menos peticiones en curso), calentamiento de 30 s para pods nuevos y expulsión temporal de pods
con fallos de conexión (no por los 503 simulados).

Panel de pruebas en la UI (CloudFront): escenarios de fallo, ráfagas, DLQ y vista en vivo de
cola, pods, HPA y nodos. El pod `api` lee Kubernetes con RBAC de solo lectura (`lab-panel-view`
en `micro`, `micro-lab-panel-nodes` para nodos) y SQS con su rol Pod Identity.

Generador `aws/load.sh` (N ventas distintas o la misma, secuencial con pausa o en paralelo,
fallos inducidos) y comandos de observación (cola, Step Functions, pods, nodos, SQL, RPS):
[CARGA.md](../CARGA.md).

## Pausar y reanudar (ahorro cuando no hay pruebas)

```bash
aws/lab-pause.sh
aws/lab-resume.sh
```

| Modo | Qué queda encendido | Costo aprox. | Volver |
|---|---|---|---|
| Encendido sin pruebas | Todo | ~$8–9/día | — |
| **Pausado** (`lab-pause.sh`) | Control plane EKS, NAT + IPv4, NLB, discos | **~$4.4/día** | `lab-resume.sh`, ~15 min |
| Borrado (runbook CloudFormation, "Eliminación") | Zonas DNS, llave KMS, snapshot RDS | ~$0.1/día | Redesplegar 01–09 + bootstrap, ~1 h |

`lab-pause.sh` apaga en orden: entrada pública (API Gateway responde 429 a todo), consumo (los mensajes esperan en SQS), pods de la app, nodos de
Karpenter (espera a que desaparezcan antes de detener a Karpenter), nodos base (stack 05
`NodeCount=0`) y RDS. `lab-resume.sh` lo invierte y abre consumo y entrada al final (throttling `THROTTLE_RATE`/`THROTTLE_BURST`, default 10/20). Nada se pierde: cola
(4 días), outbox y BD se conservan. AWS reinicia RDS solo a los 7 días detenida: para pausas más
largas, volver a correr `lab-pause.sh` o borrar el lab.

## Pendientes conocidos

- NetworkPolicy no aplicada (VPC CNI sin `enableNetworkPolicy`); el aislamiento hoy es
  mTLS + AuthorizationPolicy.
- Formato del secreto de la EventBridge Connection (`api_key_value`): verificar en el
  primer bootstrap; si cambia, el script falla explícitamente en ese paso.
- Las tres apps ejecutan `schema.sql` al arrancar; es idempotente (`IF NOT EXISTS`), pero
  un arranque simultáneo puede fallar una vez por carrera de DDL y Kubernetes lo reinicia.
