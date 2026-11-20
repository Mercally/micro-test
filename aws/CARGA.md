# Carga y observación por CLI

Generar ventas con [load.sh](load.sh) y observar cola, Lambda, Step Functions, pods, nodos,
base de datos y RPS desde la terminal. La UI lista las últimas ventas (también las de
`load.sh`) con el árbol de cambios de cada una: outbox, cada intento de paso, esperas y errores.

Requisitos: bootstrap Kubernetes hecho y consumo activo (`ConsumerEnabled=true`,
[kubernetes/README.md](kubernetes/README.md) §3). Antes de eso la API responde 503.
Herramientas: bash, curl, aws CLI, kubectl, python3 (macOS trae todo menos kubectl/aws).

## 0. Panel de pruebas (UI)

La URL de CloudFront (`SiteUrl` de 07) es el panel. Sin configuración: usa `/api/v1` del mismo origen.

| Sección | Qué hace |
|---|---|
| Sistema en vivo (cada 3 s) | Cola principal (visibles / en proceso), **DLQ** con botón *Redrive*, outbox por publicar, pasos ejecutándose, pods api/worker/mocks y HPA de KEDA, nodos por pool/capacidad/tipo (Karpenter), ventas por estado, límites de presupuesto |
| Escenario | Paso que falla (documento, CM, aseguradora), transitorio N veces (1–20) o permanente, cantidad (1–1000), envío por API pública o **ráfaga directa al outbox**. Muestra el resultado esperado y el comando CLI equivalente |
| Tiempos | Espera aleatoria por paso (`process.lab_settings`) |
| Seguimiento | Últimas ventas con árbol de cambios; botón *Reintentar* en las `FAILED` |

Cómo se encadenan los reintentos (04): 3 intentos por paso ante 503/timeout × 3 entregas SQS
(30 s entre entregas), luego DLQ. Con fallo transitorio de N intentos:

| N | Resultado |
|---|---|
| 1–2 | Se recupera con el reintento de paso (entrega 1) |
| 3–5 | Reintento global: se recupera en la entrega 2 |
| 6–8 | Se recupera en la entrega 3 |
| ≥ 9 | `FAILED` + DLQ. *Redrive* lo reprocesa (si N > 12 vuelve a fallar); *Reintentar* crea un envío nuevo sin fallo |
| permanente | 422 sin reintento de paso → 3 entregas → `FAILED` + DLQ |

**Ráfaga** (`POST /api/v1/lab/burst`, máx. 1000): inserta todas las ventas y su outbox en una
transacción. El worker las publica a SQS (~20/s por pod, `WORKER_BATCH`) y se observa la
reacción en cadena: outbox ↓, cola ↑, Lambda/Step Functions limitadas por `MaxConcurrency`,
KEDA sube pods api (hasta `API_MAX_REPLICAS`), Karpenter agrega nodos si hay pods `Pending`
(hasta `KARPENTER_CPU_LIMIT`). Nada se pierde: lo que no cabe espera en la cola o el outbox.

Los endpoints `/api/v1/lab/*` son públicos como el resto del lab (sin auth, N09): cualquiera
con la URL puede lanzar ráfagas o redrive. Throttling de API Gateway y tope de 1000 por ráfaga
limitan el abuso; para cerrarlo, Lambda authorizer (pendiente).

## 1. Generar carga — `aws/load.sh`

```bash
cd "/Volumes/Mac External/Code/github/micro-test"
export AWS_REGION=us-east-1
aws/load.sh -n 1
```

| Opción | Qué hace | Default |
|---|---|---|
| `-n N` | Cantidad de requests | 100 |
| `-c C` | Requests en paralelo (workers) | 1 (secuencial) |
| `-i MS` | Pausa de cada worker tras cada request, en ms | 0 |
| `-m distinct\|same` | `distinct`: N ventas distintas. `same`: la misma referencia N veces | distinct |
| `-f FALLO` | Encabezado `X-Lab-Failure` (`paso:tipo:veces`) | sin fallo |
| `-u URL` | URL base. Default: CloudFront (`SiteUrl`). Directo a API Gateway: `PublicApiUrl` de 06 | CloudFront |

Salida: códigos HTTP en vivo, conteo por código, latencia (avg/p50/p95/max), req/s logrados,
`saleId` distintos y la ruta del archivo con el detalle (`código tiempo saleId` por línea).

### Recetas

| Objetivo | Comando | Qué esperar |
|---|---|---|
| Humo | `aws/load.sh -n 1` | `202`, 1 saleId |
| 100 ventas distintas, una cada 200 ms (~5 rps) | `aws/load.sh -n 100 -i 200` | Todo `202` (bajo el throttling de 10 rps) |
| Misma venta 50 veces en paralelo | `aws/load.sh -n 50 -c 10 -m same` | `202` y **1** saleId: idempotencia de entrada (`external_reference` UNIQUE) |
| Ráfaga en paralelo | `aws/load.sh -n 500 -c 20` | Muchos `429`: throttling de API Gateway (10 rps, burst 20) |
| Ritmo sostenido ~8 rps | `aws/load.sh -n 480 -c 4 -i 500` | ~1 minuto de carga; observar KEDA y Karpenter |
| Fallo transitorio en CM | `aws/load.sh -n 20 -f cm:transient:2` | Ventas `COMPLETED`; reintentos visibles en Step Functions |
| Fallo permanente en Aseguradora | `aws/load.sh -n 5 -f insurer:permanent:1` | 3 entregas por mensaje → `FAILED` → DLQ → alarma |

Pasos válidos en `-f`: `document`, `cm`, `insurer`. Código `000` = sin respuesta (timeout o
red). Para pasar el límite de 10 rps, subir `ThrottlingRateLimit`/`ThrottlingBurstLimit` en 06:

```bash
aws cloudformation deploy --stack-name micro-test-ingress \
  --template-file aws/cloudformation/06-ingress.yaml \
  --parameter-overrides ThrottlingRateLimit=50 ThrottlingBurstLimit=100
```

### Experimento de backlog

Acumular mensajes sin consumir y luego soltar todo de golpe:

```bash
aws cloudformation deploy --stack-name micro-test-messaging \
  --template-file aws/cloudformation/04-messaging.yaml --capabilities CAPABILITY_IAM \
  --parameter-overrides ConsumerEnabled=false
aws/load.sh -n 300 -c 4 -i 300
aws cloudformation deploy --stack-name micro-test-messaging \
  --template-file aws/cloudformation/04-messaging.yaml --capabilities CAPABILITY_IAM \
  --parameter-overrides ConsumerEnabled=true
```

Con el consumo pausado la API sigue aceptando (`202`) y el worker publica a SQS; al activar,
el drenaje lo limita `MaxConcurrency` (04). Ver [ESCALADO.md](diagrams/ESCALADO.md).

## 2. Observar

Variables una vez por terminal:

```bash
export AWS_REGION=us-east-1
o() { aws cloudformation describe-stacks --stack-name "$1" \
  --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text; }
Q=$(o micro-test-messaging QueueUrl)
DLQ=$(o micro-test-messaging DeadLetterQueueUrl)
SM=$(o micro-test-messaging StateMachineArn)
API_ID=$(o micro-test-ingress HttpApiId)
aws eks update-kubeconfig --name micro-test --alias micro-test-aws
```

### Cola y DLQ

```bash
for u in "$Q" "$DLQ"; do aws sqs get-queue-attributes --queue-url "$u" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --query "Attributes" --output text; done
```

Primera línea cola principal, segunda DLQ: `visibles  en_proceso`.

### Step Functions

```bash
for s in RUNNING SUCCEEDED FAILED; do printf '%-10s %s\n' $s \
  "$(aws stepfunctions list-executions --state-machine-arn "$SM" --status-filter $s \
  --max-results 1000 --query 'length(executions)' --output text)"; done
```

`RUNNING` nunca supera `MaxConcurrency` (04). Cuenta hasta 1000 por estado.

### Pods, HPA y nodos

```bash
kubectl -n micro get scaledobject,hpa,pods -o wide
kubectl get nodes -L karpenter.sh/nodepool,karpenter.sh/capacity-type,node.kubernetes.io/instance-type
kubectl top pods -n micro
```

En vivo en otra terminal: `kubectl -n micro get pods -w` y `kubectl get nodes -w`.

### Base de datos (SQL)

RDS no tiene Data API: se consulta con un pod efímero de `psql` que lee el secreto `db`
de Kubernetes (la contraseña no pasa por la terminal). Definir la función una vez:

```bash
sql() { kubectl -n micro run "psql-$RANDOM" --rm -i --quiet --restart=Never \
  --image=postgres:16-alpine --labels=sidecar.istio.io/inject=false \
  --overrides='{"spec":{"nodeSelector":{"role":"base"},"containers":[{"name":"psql","image":"postgres:16-alpine","stdin":true,"stdinOnce":true,"command":["sh","-c","PGPASSWORD=\"$DATABASE_PASSWORD\" psql \"${DATABASE_URL#jdbc:}\" -U \"$DATABASE_USERNAME\" -P pager=off"],"envFrom":[{"secretRef":{"name":"db"}}]}]}}'; }
```

Consultas útiles (cada una tarda ~5 s por el arranque del pod):

```bash
echo "select status, count(*) from endpoint0.sales group by 1 order by 1;" | sql
echo "select count(*) filter (where published_at is null) pendientes, count(*) publicados, max(attempts) max_intentos from endpoint0.outbox;" | sql
echo "select step, outcome, count(*) from process.steps group by 1,2 order by 1,2;" | sql
echo "select count(*) conexiones, max_conn from pg_stat_activity, (select setting::int max_conn from pg_settings where name='max_connections') m group by max_conn;" | sql
echo "select status, count(*) from endpoint0.sales where external_reference like 'LOAD-093508-%' group by 1;" | sql
```

La última filtra una corrida: reemplazar `093508` por el `run=` que imprime `load.sh`.

### RPS y errores de la API pública (CloudWatch, por minuto)

```bash
for m in Count 4xx 5xx; do echo "== $m"; aws cloudwatch get-metric-statistics \
  --namespace AWS/ApiGateway --metric-name $m --dimensions Name=ApiId,Value="$API_ID" \
  --start-time "$(date -u -v-15M +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" \
  --period 60 --statistics Sum --query 'sort_by(Datapoints,&Timestamp)[].[Timestamp,Sum]' --output text; done
```

`Count/60` = req/s de ese minuto. Las métricas llegan con ~1 min de retraso.

### Lambda consumidora

```bash
aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name ConcurrentExecutions \
  --dimensions Name=FunctionName,Value=micro-test-consumer \
  --start-time "$(date -u -v-15M +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" \
  --period 60 --statistics Maximum --query 'sort_by(Datapoints,&Timestamp)[].[Timestamp,Maximum]' --output text
aws logs tail /aws/lambda/micro-test-consumer --since 10m --format short
```

### Todo junto, cada 10 s

```bash
while true; do clear; date
  aws sqs get-queue-attributes --queue-url "$Q" --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --query Attributes --output text
  aws stepfunctions list-executions --state-machine-arn "$SM" --status-filter RUNNING --query 'length(executions)' --output text
  kubectl -n micro get hpa --no-headers; kubectl get nodes --no-headers | wc -l
  sleep 10; done
```

Ctrl+C para salir. Vista gráfica equivalente: dashboard CloudWatch `micro-test-lab`
([OBSERVABILIDAD.md](diagrams/OBSERVABILIDAD.md)).

## 3. Duración de cada paso (configurable en vivo)

Antes de cada paso de negocio (documento, CM, Aseguradora) la API espera un tiempo
aleatorio entre `step_delay_min_ms` y `step_delay_max_ms`. Se lee en cada llamada: el cambio
aplica al instante, sin reiniciar pods. Default 0–10000 ms; máximo permitido 15000 (el
timeout del paso en Step Functions es 20 s).

```bash
echo "select * from process.lab_settings;" | sql
echo "update process.lab_settings set step_delay_min_ms=0, step_delay_max_ms=0;" | sql
echo "update process.lab_settings set step_delay_min_ms=500, step_delay_max_ms=3000;" | sql
```

La segunda línea deja todo lo más rápido posible; la tercera, entre 0.5 y 3 s por paso.
Más espera = ejecuciones más largas = menos ventas/s con el mismo `MaxConcurrency` (04).

Cada intento queda en `process.history` (`RUNNING` → `DONE`/`RECONCILED`/`ALREADY_DONE`/`ERROR`,
con espera y duración total). La UI lo muestra como árbol por venta; por SQL:

```bash
echo "select step, outcome, count(*), round(avg(delay_ms)) espera_ms, round(avg(duration_ms)) total_ms from process.history group by 1,2 order by 1,2;" | sql
```

## 4. Limpiar datos de carga (opcional)

Las ventas quedan en la BD; no molestan. Para borrar una corrida y su rastro:

```bash
echo "begin;
delete from process.history where sale_id in (select id from endpoint0.sales where external_reference like 'LOAD-093508-%');
delete from process.steps where sale_id in (select id from endpoint0.sales where external_reference like 'LOAD-093508-%');
delete from endpoint0.outbox where sale_id in (select id from endpoint0.sales where external_reference like 'LOAD-093508-%');
delete from endpoint0.sales where external_reference like 'LOAD-093508-%';
commit;" | sql
```

Si alguna tabla con FK a ventas bloquea el borrado (endpoint1–5, mock, lab_faults), el
`commit` falla completo y no se borra nada.
