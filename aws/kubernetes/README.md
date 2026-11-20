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
aws/kubernetes/validate.sh    # ~45 s; Docker + kind + helm + kubectl; cluster desechable (~1.5 GB)
```

Termina en `VALIDATION OK`. Ejecutarlo tras cambiar manifiestos o versiones de charts.

## 1. Imagen arm64 en tu ECR

Los nodos son Graviton: la imagen debe ser `linux/arm64` (nativa en Mac Apple Silicon).

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REPO=<tu-repositorio-ecr>
TAG=$(git rev-parse --short HEAD)
export IMAGE=$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/$REPO:$TAG

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
sleep 20; curl -s "$SITE/api/v1/purchases/$SALE" | python3 -m json.tool   # status COMPLETED, 3 steps
```

También desde la UI: `$SITE` con URL base `/api/v1`.

## Perillas del laboratorio

| Perilla | Dónde | Efecto |
|---|---|---|
| `MaxConcurrency` | 04-messaging | Ejecuciones simultáneas que entran a Step Functions |
| `ConsumerEnabled` | 04-messaging | `false`: la cola acumula; `true`: drena |
| `ThrottlingRateLimit` / `ThrottlingBurstLimit` | 06-ingress | Tasa máxima de compras por la API pública |
| `MaxCapacity` | 03-data | Techo de ACU de Aurora |
| `API_MAX_REPLICAS`, `API_MESSAGES_PER_POD` | bootstrap.sh | Pods de api y mensajes SQS por pod antes de escalar |
| `KARPENTER_CPU_LIMIT` | bootstrap.sh | vCPU máximas que Karpenter puede lanzar |
| `MOCKS_FAILURE_PROBABILITY`, `MOCKS_LATENCY_MS` | bootstrap.sh | Fallos raros y lentitud de CM/Aseguradora |

## Simular fallos

```bash
# Fallo transitorio 2 veces en el paso CM (se recupera con los reintentos del paso)
curl -s -X POST "$SITE/api/v1/purchases" -H 'Content-Type: application/json' -H 'X-Lab-Failure: cm:transient:2' \
  -d '{"externalReference":"AWS-F1","customerName":"Lab","customerEmail":"lab@example.com","productCode":"AUTO-01"}'
# Fallo permanente en Aseguradora (3 entregas → FAILED → DLQ → alarma)
#   -H 'X-Lab-Failure: insurer:permanent:1'
kubectl -n micro delete pod -l app=api                     # caída de la API
kubectl -n micro scale deploy/mocks --replicas=0           # CM/Aseguradora caídos
kubectl delete node -l karpenter.sh/nodepool=workloads     # pérdida de nodo: Karpenter repone
```

Pasos válidos para `X-Lab-Failure`: `document`, `cm`, `insurer`.

## Carga y escalado

```bash
for i in $(seq 1 200); do
  curl -s -o /dev/null -w '%{http_code} ' -X POST "$SITE/api/v1/purchases" -H 'Content-Type: application/json' \
    -d "{\"externalReference\":\"LOAD-$i-$RANDOM\",\"customerName\":\"Lab\",\"customerEmail\":\"lab@example.com\",\"productCode\":\"AUTO-01\"}"
done; echo
```

Observar en paralelo (429 = throttling de la API pública, es esperado):

```bash
kubectl get nodes -L karpenter.sh/capacity-type,node.kubernetes.io/instance-type -w
kubectl -n micro get hpa,pods -w           # HPA creado por KEDA
aws sqs get-queue-attributes --queue-url "$(aws cloudformation list-exports \
  --query "Exports[?Name=='micro-test-messaging-QueueUrl'].Value" --output text)" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

## Detener (sin borrar)

```bash
# 1. Consumo pausado: ConsumerEnabled=false (04)
# 2. App a cero: KEDA respeta la anotación de pausa
kubectl -n micro annotate scaledobject api autoscaling.keda.sh/paused-replicas=0 --overwrite
kubectl -n micro scale deploy/worker deploy/mocks --replicas=0
```

Sin pods de la app, Karpenter consolida y elimina sus nodos (~1 min) y Aurora puede
pausarse (requiere cero conexiones). Siguen cobrando: control plane EKS, nodos base, NAT
y NLB. Para reanudar: quitar la anotación (`autoscaling.keda.sh/paused-replicas-`) y
escalar worker/mocks a 1. Para eliminar todo: ver orden de eliminación del runbook.

## Pendientes conocidos

- NetworkPolicy no aplicada (VPC CNI sin `enableNetworkPolicy`); el aislamiento hoy es
  mTLS + AuthorizationPolicy.
- Formato del secreto de la EventBridge Connection (`api_key_value`): verificar en el
  primer bootstrap; si cambia, el script falla explícitamente en ese paso.
- Las tres apps ejecutan `schema.sql` al arrancar; es idempotente (`IF NOT EXISTS`), pero
  un arranque simultáneo puede fallar una vez por carrera de DDL y Kubernetes lo reinicia.
