# Runbook — desplegar los stacks uno a uno

Orden exacto, comandos y pasos manuales del laboratorio AWS (`us-east-1`). Cada
stack importa Outputs de los anteriores por **nombre de stack**: usar exactamente
los nombres de la tabla. Decisiones y estado: [PLAN_AWS.md](../PLAN_AWS.md).
Revisión con change sets, fallos y rollback: [CLOUDFORMATION.md](../CLOUDFORMATION.md) §4–5.

| Orden | Plantilla | Stack | Depende de | IAM | Paso manual | Costo fijo aprox. |
|---|---|---|---|---|---|---|
| 00 | `00-dns.yaml` | `micro-test-dns` | — | No | **Sí**: nameservers de `mercally.dev` en Hostinger ([DNS.md](../DNS.md)) | ~$1/mes (2 zonas) |
| 01 | `01-network.yaml` | `micro-test-network` | — | No | No | NAT ~$33/mes + datos |
| 02 | `02-security.yaml` | `micro-test-security` | — | No | No | KMS ~$1/mes |
| 03 | `03-data.yaml` | `micro-test-data` | 01, 02 | No | Plan de cuenta pagado | RDS db.t4g.micro ~$12/mes + gp3 20 GiB ~$2; secreto ~$0.40/mes |
| 04 | `04-messaging.yaml` | `micro-test-messaging` | 02 | **Sí** | Generar `WorkflowApiKey` | Secreto Connection ~$0.40/mes; resto por uso |
| 05 | `05-eks.yaml` | `micro-test-eks` | 01, 02, 03, 04 | **Sí** | IP pública para kubectl | Control plane ~$73/mes + 2× t4g.medium ~$49/mes + EBS ~$3 |
| 06 | `06-ingress.yaml` | `micro-test-ingress` | 00 (delegación verificada), 01 | No | Actualizar 04 con sus Outputs | NLB ~$16/mes + LCU; Resource Gateway por hora/GB (verificar); API GW por request |
| 07 | `07-frontend.yaml` | `micro-test-frontend` | 06 | No | Build + subir Angular | Centavos: CloudFront y S3 por uso |
| 08 | `08-observability.yaml` | `micro-test-observability` | 04, 05, 06 | No | — | Dashboard ~$3/mes; Application Signals y Container Insights por uso |
| 09 | `09-registry.yaml` | `micro-test-registry` | — (cualquier momento antes de subir la imagen) | No | — | ECR ~$0.10/GB-mes (últimas 10 imágenes) |

Sin costos ocultos: 00 y 02 retienen sus recursos al borrar (ver Eliminación).

## Antes de empezar

```bash
cd <raíz del repo>
export AWS_REGION=us-east-1
aws sts get-caller-identity
localstack/.venv/bin/cfn-lint --regions us-east-1 -- aws/cloudformation/*.yaml
python3 -m unittest aws/tests/test_consumer.py
```

Validación local de la aplicación (Docker solo para un PostgreSQL efímero; sin AWS):

```bash
docker run -d --rm --name micro-test-pg -e POSTGRES_DB=docs_business_test \
  -e POSTGRES_USER=docs -e POSTGRES_PASSWORD=docs -p 127.0.0.1:55432:5432 postgres:16-alpine
until docker exec micro-test-pg pg_isready -U docs -d docs_business_test >/dev/null 2>&1; do sleep 1; done
LAB_TEST_POSTGRES=true DATABASE_URL=jdbc:postgresql://127.0.0.1:55432/docs_business_test \
  DATABASE_USERNAME=docs DATABASE_PASSWORD=docs mvn test
docker stop micro-test-pg
(cd frontend && npm run build)
```

| Suite | Qué prueba |
|---|---|
| `ProcessIntegrationTest` | Pasos idempotentes: reentrega, acuse perdido, concurrencia, orden, cleanup |
| `OutboxPublisherIntegrationTest` | Worker: envío único, backoff ante fallo SQS, batch, pods concurrentes sin duplicados |
| `OutboxWorkerWiringTest` | Con `WORKER_ENABLED=true` Spring arma el worker con el bean `SqsClient` |
| `PurchaseIntegrationTest` | Aceptación 202 + outbox (flujo existente) |
| `aws/tests/test_consumer.py` | Lambda: lease, espera de su ejecución (admisión), duplicados, body inválido |
| `aws/kubernetes/validate.sh` | Manifiestos Kubernetes contra CRDs reales en kind; versiones y flags de Helm |

`aws cloudformation deploy` crea y ejecuta un change set. Para revisar antes de
aplicar, añadir `--no-execute-changeset` y seguir [CLOUDFORMATION.md](../CLOUDFORMATION.md) §4.3.

## 00 — DNS

```bash
aws cloudformation deploy --stack-name micro-test-dns \
  --template-file aws/cloudformation/00-dns.yaml
aws cloudformation describe-stacks --stack-name micro-test-dns \
  --query "Stacks[0].Outputs[?OutputKey=='NameServers'].OutputValue" --output text
```

**Manual:** copiar registros existentes, cambiar nameservers en Hostinger y verificar — pasos 1–3 de [DNS.md](../DNS.md).
Puede hacerse en paralelo con 01–05; debe estar verificado antes de 06.

## 01 — Red

```bash
aws cloudformation deploy --stack-name micro-test-network \
  --template-file aws/cloudformation/01-network.yaml
```

El NAT empieza a cobrar al crearse.

## 02 — KMS

```bash
aws cloudformation deploy --stack-name micro-test-security \
  --template-file aws/cloudformation/02-security.yaml
```

## 03 — RDS PostgreSQL y S3 temporal

RDS PostgreSQL single-AZ `db.t4g.micro` (N23). La cuenta debe estar en **plan pagado**:
el Free plan bloquea este diseño (Aurora solo con express configuration, sin VPC Lattice).

```bash
aws rds describe-db-engine-versions --engine postgres --default-only \
  --query "DBEngineVersions[].EngineVersion" --output text

aws cloudformation deploy --stack-name micro-test-data \
  --template-file aws/cloudformation/03-data.yaml
```

La primera línea debe mostrar una versión `16.x`; si la versión por defecto ya no es 16,
desplegar igual (el parámetro `EngineVersion=16` fija la mayor). Tarda ~10 min. Verificar:

```bash
aws rds describe-db-instances --db-instance-identifier micro-test-db \
  --query "DBInstances[0].[DBInstanceStatus,EngineVersion,DBInstanceClass,AvailabilityZone]" --output text
```

Debe mostrar `available`. RDS no tiene Data API: la prueba SQL real ocurre cuando arrancan
los pods (bootstrap). Para cambiar el tamaño (perilla del lab): redeploy con
`--parameter-overrides DbInstanceClass=db.t4g.small` (reinicio ~5 min).

## 04 — Mensajería

Hasta que exista 06 la API privada no es alcanzable: desplegar con el consumo
**pausado** (`ConsumerEnabled=false`) y `ResourceConfigurationArn` vacío.

```bash
WORKFLOW_KEY=$(openssl rand -hex 24)

aws cloudformation deploy --stack-name micro-test-messaging \
  --template-file aws/cloudformation/04-messaging.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    ApiBaseUrl=https://api-internal.aws.mercally.dev \
    WorkflowApiKey="$WORKFLOW_KEY" \
    ConsumerEnabled=false
unset WORKFLOW_KEY
```

En actualizaciones posteriores **omitir** `WorkflowApiKey`: `deploy` conserva el valor
previo de todo parámetro no incluido en `--parameter-overrides`. Pasarlo solo para rotarla.

Prueba sin API (comprueba cola, ESM pausado y DLQ):

```bash
aws cloudformation describe-stacks --stack-name micro-test-messaging \
  --query "Stacks[0].Outputs" --output table
```

Al terminar 06: actualizar con `ResourceConfigurationArn=<output de 06>` y `ConsumerEnabled=true`.

## 05 — EKS

Crea cluster, 2 nodos base On-Demand en AZ A, add-ons gestionados (vpc-cni, kube-proxy,
coredns, pod identity agent, metrics-server), roles Pod Identity por workload y la cola
de interrupciones de Karpenter. **No** instala Istio, KEDA, Karpenter, LBC ni la app:
eso es el bootstrap Kubernetes (siguiente etapa).

```bash
aws eks describe-cluster-versions --default-only --query "clusterVersions[].clusterVersion" --output text

ADMIN_CIDR="$(curl -s https://checkip.amazonaws.com)/32"

aws cloudformation deploy --stack-name micro-test-eks \
  --template-file aws/cloudformation/05-eks.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides AdminCidr="$ADMIN_CIDR"
```

Tarda ~15–20 min. La identidad que despliega queda como admin del cluster. Verificar:

```bash
aws eks update-kubeconfig --name micro-test --alias micro-test-aws
kubectl get nodes -L role,topology.kubernetes.io/zone
kubectl get pods -n kube-system
aws eks list-pod-identity-associations --cluster-name micro-test --output table
```

Si cambia tu IP pública, kubectl deja de conectar: redeploy con el nuevo `AdminCidr`.

Pod Identity creado (las service accounts las crean después Helm/manifiestos):

| Namespace / ServiceAccount | Permisos |
|---|---|
| `micro/api` | S3 `tmp/*` get/put/delete + list bucket; secreto BD; KMS |
| `micro/worker` | `sqs:SendMessage` cola principal; `s3:ListBucket` (HeadBucket al arrancar); secreto BD; KMS |
| `micro/mocks` | `s3:ListBucket` (HeadBucket al arrancar); secreto BD; KMS |
| `keda/keda-operator` | `sqs:GetQueueAttributes`; secreto BD (scaler outbox); KMS |
| `kube-system/aws-load-balancer-controller` | Solo TargetGroupBinding: describe + register/deregister en target groups `micro-test-*` |
| `kube-system/karpenter` | Lanzar/terminar sus nodos, PassRole del node role, perfiles de instancia, cola de interrupciones |

## 06 — Ingress

Requisito: `dig @1.1.1.1 NS aws.mercally.dev +short` ya devuelve los servidores `awsdns`
([DNS.md](../DNS.md) paso 3). Si no, el stack queda esperando la validación ACM hasta fallar.

```bash
aws cloudformation deploy --stack-name micro-test-ingress \
  --template-file aws/cloudformation/06-ingress.yaml
aws cloudformation describe-stacks --stack-name micro-test-ingress \
  --query "Stacks[0].Outputs" --output table
```

Crea: certificado ACM `api-internal.aws.mercally.dev` (validación DNS automática), registro
A alias → NLB interno, NLB con listener TLS 443 → target group IP puerto 8080 (Istio gateway,
health check `15021/healthz/ready`), API Gateway HTTP con 4 rutas públicas (crear, listar, consultar, reintentar) y throttling
10 rps / burst 20, Resource Gateway + Resource Configuration (VPC Lattice).

El target group queda **sin targets** hasta el bootstrap Kubernetes (TargetGroupBinding
con `IstioTargetGroupArn`). Mientras tanto el API público responde 503.

Conectar la Step Function por la ruta privada (04 conserva `WorkflowApiKey`):

```bash
RC_ARN=$(aws cloudformation describe-stacks --stack-name micro-test-ingress \
  --query "Stacks[0].Outputs[?OutputKey=='ResourceConfigurationArn'].OutputValue" --output text)
aws cloudformation deploy --stack-name micro-test-messaging \
  --template-file aws/cloudformation/04-messaging.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides ResourceConfigurationArn="$RC_ARN"
```

`ConsumerEnabled` sigue en `false` hasta que la app esté desplegada en EKS.

## 07 — Frontend

CloudFront sirve Angular desde S3 privado (OAC) y enruta `/api/*` al API Gateway del 06:
la SPA usa `/api/v1` en su propio origen (sin CORS ni URL de API dentro del build).

```bash
aws cloudformation deploy --stack-name micro-test-frontend \
  --template-file aws/cloudformation/07-frontend.yaml

out() { aws cloudformation describe-stacks --stack-name micro-test-frontend \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }

(cd frontend && npm ci && npm run build)
aws s3 sync frontend/dist/sales-ui/browser "s3://$(out SiteBucketName)" --delete
aws cloudfront create-invalidation --distribution-id "$(out DistributionId)" --paths '/*'
echo "$(out SiteUrl)"
```

La primera creación de la distribución tarda ~5–10 min. En la UI, la URL base es
`/api/v1` (valor por defecto fuera de localhost) y el JWT queda vacío. Hasta el bootstrap
Kubernetes las llamadas responden 503 (NLB sin targets).

Para volver a publicar tras cambios: repetir build, `sync` e invalidación.

## 08 — Observabilidad

```bash
aws cloudformation deploy --stack-name micro-test-observability \
  --template-file aws/cloudformation/08-observability.yaml
```

Activa Application Signals en la cuenta y crea el dashboard `micro-test-lab`. El add-on de
CloudWatch (agente, Fluent Bit, inyección del agente ADOT) ya lo instala 05. Qué ver y
dónde: [OBSERVABILIDAD.md](../diagrams/OBSERVABILIDAD.md).

## Después de 07 — Kubernetes

Imagen arm64 en ECR, bootstrap (Istio, LBC, KEDA, Karpenter, app), activar consumo,
pruebas, perillas, fallos y carga: [aws/kubernetes/README.md](../kubernetes/README.md).

## Eliminación (orden inverso)

Antes de borrar 05: eliminar NodePools de Karpenter y esperar que sus nodos terminen
(`kubectl delete nodepools --all`); las instancias de Karpenter no pertenecen a CloudFormation.

```bash
aws s3 rm "s3://micro-test-frontend-$(aws sts get-caller-identity --query Account --output text)-us-east-1" --recursive
aws cloudformation delete-stack --stack-name micro-test-registry
aws cloudformation delete-stack --stack-name micro-test-observability
aws cloudformation delete-stack --stack-name micro-test-frontend
aws cloudformation deploy --stack-name micro-test-messaging --template-file aws/cloudformation/04-messaging.yaml \
  --capabilities CAPABILITY_IAM --parameter-overrides ResourceConfigurationArn=''
aws cloudformation delete-stack --stack-name micro-test-ingress
aws cloudformation delete-stack --stack-name micro-test-eks
aws cloudformation delete-stack --stack-name micro-test-messaging
aws cloudformation delete-stack --stack-name micro-test-data
aws cloudformation delete-stack --stack-name micro-test-security
aws cloudformation delete-stack --stack-name micro-test-network
aws cloudformation delete-stack --stack-name micro-test-dns
```

Esperar cada uno con `aws cloudformation wait stack-delete-complete --stack-name <stack>`.
El bucket de documentos debe estar vacío para borrar 03. Recursos retenidos a revisar
manualmente si ya no se usan: snapshot de RDS, llave KMS (programar borrado, 7–30 días)
y zona Route 53.
