# Ejercicios de flujo y escalado

Instalación única: [../README.md](../README.md), `./localstack/lab up`.
HPA, Karpenter y prueba de worker real forman parte obligatoria del arranque nuevo.
En laboratorio anterior todavía debes ejecutar y aportar sus reportes; haber
aplicado un ZIP no prueba instalación ni escalado.

## HPA: CPU y memoria

`hpa.yaml`: contenedor `micro`, CPU 60% del request y memoria `AverageValue` 512Mi;
1–4 réplicas, subida 1 pod/60 s y reducción 1 pod/60 s con estabilización 300 s.
Requests actuales: CPU 100m, memoria 384Mi; límite memoria 768Mi. Sidecar Istio no
entra en esas métricas. HPA usa máximo de recomendaciones: CPU **o** RAM pueden
pedir más pods. Umbral RAM es inicial, no resultado de dimensionamiento.

En WSL, desde raíz, después de bootstrap:

```bash
export PATH="$PWD/localstack/.state/tools:$HOME/.local/bin:$PATH"
kubectl --kubeconfig localstack/.state/kubeconfig -n micro-test-local-general top pods --containers
kubectl --kubeconfig localstack/.state/kubeconfig -n micro-test-local-general get hpa,pods -w
```

En segunda terminal:

```bash
localstack/.venv/bin/python localstack/scaling/exercise.py \
  --endpoint http://127.0.0.1:4566 --count 20 --rate 2
```

Observar métricas, réplicas deseadas/actuales, Pending y recuperación después de
retirar carga. Pico corto puede no escalar. Medir memoria con JVM caliente, bajo
carga y tras retirar carga; GC/heap retenido puede impedir reducción. Si memoria
base supera umbral, añadir réplicas no resuelve problema. Revisar OOM/reinicios.
HPA no corrige fugas ni garantiza prevenir OOM individual.

Para cambiar umbral usa red real indicada en `bootstrap.json`, no copies sufijo
de otro clúster:

```bash
localstack/.venv/bin/python localstack/scaling/runtime.py hpa \
  --network RED_K3D_OBSERVADA --memory-target-mib 550
```

Instalador exige CPU/RAM observables y umbral por debajo del límite del micro.
Para escalado manual, elimina solo HPA del micro, cambia réplicas y reaplica HPA
al terminar. No competir con reconciliación HPA:

```bash
kubectl --kubeconfig localstack/.state/kubeconfig -n micro-test-local-general delete hpa micro-test-local-micro
kubectl --kubeconfig localstack/.state/kubeconfig -n micro-test-local-general scale deployment/micro-test-local-micro --replicas=2
```

## Karpenter: nodos reales

Provider AWS 1.8.2 contra LocalStack, mismo EKS. NodePool de práctica con taint
`dedicated=karpenter-practice`, instancia `t3.medium`, límite CPU 2.
No usa KWOK. NodeClaim Ready por sí solo no basta: `node-test` también verifica
providerID/EC2 local y respuesta HTTP desde nginx en worker nuevo.

```bash
localstack/.venv/bin/python localstack/scaling/runtime.py node-test --network RED_K3D_OBSERVADA
kubectl --kubeconfig localstack/.state/kubeconfig get nodepools,nodeclaims,nodes
kubectl --kubeconfig localstack/.state/kubeconfig -n micro-test-local-karpenter logs deployment/karpenter --tail=100
```

Si aún operas laboratorio anterior, instalación explícita de componente pendiente:
`runtime.py karpenter --network RED_K3D_OBSERVADA`, seguida de `node-test`.
Esto sí modifica Kubernetes existente; no es necesario para probar compra por NLB.

Micro sigue en nodos administrados general. Karpenter se ejercita con nginx para
validar capacidad real sin mover aplicación cuya imagen se importa a nodos actuales.
Límite CPU no constituye cuota física estricta de Docker. Consolidación y expiración
inicialmente deshabilitadas. Para practicar eliminación **solo del pool de prueba**:

```bash
kubectl --kubeconfig localstack/.state/kubeconfig -n micro-test-local-karpenter scale deployment/micro-test-local-practice --replicas=0
kubectl --kubeconfig localstack/.state/kubeconfig patch nodepool micro-test-local-practice \
  --type=merge -p '{"spec":{"disruption":{"consolidationPolicy":"WhenEmpty","consolidateAfter":"60s","budgets":[{"nodes":"1"}]}}}'
kubectl --kubeconfig localstack/.state/kubeconfig get nodeclaims,nodes -w
```

Comprobar desaparición del worker Docker/instancia además del objeto Kubernetes.
Reejecutar instalador restaura política inicial. No drenar general/onprem/terceros
como si pertenecieran al pool Karpenter. No se certifica consolidación sin reporte.

## Pipes y capacidad

Outbox → Lambda publisher → SQS → Pipe → Step Functions Standard. No Event Bus.
Lote 1, ventana 0, FIRE_AND_FORGET. Cola vacía no significa micros libres: Pipe
confirma inicio de workflow, no espera finalización. Visibility timeout y DLQ
atienden entrega/admisión; fallos posteriores del workflow no van automáticamente
al DLQ de SQS. Recuperación de compra usa estado de aplicación y retry explícito.

Pipes/SQS no expone `maxExecutionsPerSecond` ni `MaximumConcurrency` del mapping
SQS/Lambda. Ventana de lote no limita tasa. HPA/Karpenter reaccionan a métricas y
pods pendientes; no predicen picos ni controlan admisión.

```bash
localstack/.venv/bin/python localstack/scaling/flow.py observe --endpoint http://127.0.0.1:4566
localstack/.venv/bin/python localstack/scaling/flow.py estimate \
  --rate 5 --cpu-seconds 0.2 --cpu-request 0.1 --target 0.6 --max 4
```

Estimación ilustrativa: demanda 17 réplicas supera máximo 4. CPU-segundos deben
medirse; fórmula de estado estable ignora DB, esperas, ráfagas y retries.
Medir p95/p99 de aceptación por separado de duración del workflow.

Para acumulación, pausa solo Pipe (publisher continúa), envía compras y observa
cola; reanudación puede provocar ráfaga:

```bash
localstack/.venv/bin/python localstack/scaling/flow.py pipe-pause --endpoint http://127.0.0.1:4566
localstack/.venv/bin/python localstack/scaling/flow.py observe --endpoint http://127.0.0.1:4566
localstack/.venv/bin/python localstack/scaling/flow.py pipe-resume --endpoint http://127.0.0.1:4566
```

## NLB

`flow.py probe --endpoint http://127.0.0.1:4566` comprueba HTTP Task por listener TCP
observado. 4566 es gateway de APIs; NLB puede usar 4512 u otro puerto asignado.
No necesita publicación de ese puerto hacia host. Target local es IP Docker de
nodo Ready/NodePort 31080 → Istio. No hay reconciliación continua de targets.

`runtime.py nlb` cambia targets: no usarlo como simple inspección. Requiere Pipe
detenido y workflows drenados si cambia ruta activa; detén también nuevas requests
API. Si salud falla restaura targets anteriores. Pod IP directo es experimento,
no configuración por defecto ni prueba de conectividad VPC AWS.

Referencias: [HPA ContainerResource](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#container-resource-metrics),
[Pipes SQS](https://docs.aws.amazon.com/eventbridge/latest/pipes-reference/API_PipeSourceSqsQueueParameters.html),
[LocalStack Karpenter](https://docs.localstack.cloud/aws/services/eks/#self-managed-nodes-and-karpenter).
