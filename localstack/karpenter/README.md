# Karpenter con workers locales reales en WSL2

Solicitud confirmada: **nodos reales que ejecuten contenedores**. Este módulo usa
Karpenter provider Cluster API → MachineDeployment → CAPD → Docker. Cada worker
tiene kubelet/containerd; la prueba exige ejecutar HTTP dentro de sus pods.

**Estado: implementación y validación estática; runtime pendiente en tu laptop.**
No se ha ejecutado build Docker, instalado este clúster ni aprobado escalado real.
El proveedor es experimental. Se fija su matriz publicada, no las versiones más
recientes: provider CAPI `v0.2.0`, CAPI/CAPD `v1.10.1`, Kubernetes `v1.33.1`,
kind `v0.29.0`, Go `1.24.2` para compilación y Calico `v3.30.0`.

## Relación con compras e Istio

El flujo de compra y sus pruebas Istio permanecen en EKS/k3d existente de
LocalStack. **Este Karpenter no escala esos Managed Node Groups**: crea un clúster
CAPD adicional. No se ha migrado el micro a CAPD ni se presenta el HTTP de práctica
como prueba del PDF. Primero completa [compras](../business/RUNBOOK.md); este
ejercicio permite observar creación/borrado de workers reales sin reemplazar EKS.

Recursos adicionales: un control plane kind de administración, un control plane
CAPD, un load balancer Docker, controlador Karpenter y dos workers para práctica.
Comparte CPU/RAM de Docker Desktop; deja capacidad para LocalStack/EKS/DB/Istio.
Las anotaciones 2 CPU/4Gi describen el modelo de scheduling, **no reservas ni
límites físicos por nodo**. El instalador calcula límites del NodePool como tres
veces CPU/RAM observadas en Docker porque cada nodo reporta capacidad compartida.
Son límites de Karpenter eventualmente consistentes, no un máximo físico estricto.

## Preparar herramientas en WSL

Ejecuta desde raíz de este repositorio dentro de WSL2, Docker Desktop integrado.
Requiere Python con `localstack/requirements.txt`, Git, curl y kubectl compatible
con Kubernetes 1.33. Los scripts de compras también necesitan k3d e istioctl.

Si faltan las versiones fijadas de kind/clusterctl, instálalas **solo para este
repositorio**, sin reemplazar herramientas globales:

```bash
mkdir -p localstack/.state/karpenter-tools
curl --fail --location --proto '=https' --proto-redir '=https' \
  https://github.com/kubernetes-sigs/kind/releases/download/v0.29.0/kind-linux-amd64 \
  -o localstack/.state/karpenter-tools/kind
curl --fail --location --proto '=https' --proto-redir '=https' \
  https://github.com/kubernetes-sigs/cluster-api/releases/download/v1.10.1/clusterctl-linux-amd64 \
  -o localstack/.state/karpenter-tools/clusterctl
chmod +x localstack/.state/karpenter-tools/kind localstack/.state/karpenter-tools/clusterctl
export PATH="$PWD/localstack/.state/karpenter-tools:$PATH"
kind version
clusterctl version -o short
```

La descarga de dependencias/imágenes públicas requiere Internet; no crea recursos
AWS. No se instala proveedor AWS ni se entregan credenciales AWS al controlador.
Cada comando runtime verifica primero LocalStack local, cuenta `000000000000`,
Docker Unix socket local y WSL2 amd64. El perfil de trabajo sigue siendo
`localstack-laptop`; el script usa credenciales ficticias y endpoint explícito
`http://127.0.0.1:4566`, sin heredar sesiones reales.

## Instalar y comprobar

```bash
# Solo fuentes fijadas por commit y Calico verificado por SHA-256; sin clúster.
localstack/.venv/bin/python localstack/karpenter/karpenter.py prepare
localstack/.venv/bin/python localstack/karpenter/karpenter.py validate

# Construye dos imágenes locales e instala clúster adicional; no reinicia LocalStack.
localstack/.venv/bin/python localstack/karpenter/karpenter.py install
localstack/.venv/bin/python localstack/karpenter/karpenter.py start

# Dos pods con anti-affinity obligatoria: necesitan dos workers distintos.
localstack/.venv/bin/python localstack/karpenter/karpenter.py load
localstack/.venv/bin/python localstack/karpenter/karpenter.py test
localstack/.venv/bin/python localstack/karpenter/karpenter.py status
```

`install` guarda recibo de identidad de ambos clústeres y hashes de manifiestos.
Repetir instalación completa no vuelve a fijar réplicas de workers en cero.
Si falla, conserva recursos y reporte; no borra ni adopta clústeres ajenos.
No aplicar `cluster.yaml` manualmente sobre workers activos: su bootstrap declara
`replicas: 0`; después de instalar, Karpenter controla ese campo.

CAPD upstream publica su load balancer en todas las interfaces. `CAPD.Dockerfile`
cambia exclusivamente ese bind a `127.0.0.1`/`::1`; instalación comprueba puertos
resultantes. Karpenter corre en contenedor conectado a red Docker observada,
**sin puertos publicados**. Kubeconfigs usan TLS con certificados, permisos 0600,
sin helpers AWS y sin modificar `~/.kube/config`. Controlador tiene acceso admin
a estos dos clústeres de laboratorio; CAPD necesita Docker socket para crear nodos.

## Qué observar

Usa terminal adicional mientras ejecutas `load`:

```bash
export LAB_CAPD_MGMT="$PWD/localstack/.state/karpenter-capd/micro-test-local-capi-management.yaml"
export LAB_CAPD_WORKLOAD="$PWD/localstack/.state/karpenter-capd/micro-test-local-capd.yaml"

kubectl --kubeconfig "$LAB_CAPD_WORKLOAD" get nodepools,nodeclaims,nodes -w
# En otra terminal:
kubectl --kubeconfig "$LAB_CAPD_MGMT" -n micro-test-local-capd get machines,machinedeployments
docker logs --tail 100 -f micro-test-local-karpenter-controller
docker ps --filter label=io.x-k8s.kind.cluster=micro-test-local-capd
```

Secuencia esperada: pods Pending → NodeClaims → MD incrementa réplicas → Machines
y contenedores Docker → Nodes Ready → pods ejecutando HTTP.

`test` exige dos pods Ready en nodos diferentes, NodeClaims Ready, coincidencia de
providerID en Node/Machine/NodeClaim, contenedor Docker propio encendido, petición
HTTP mediante `kubectl exec` y marcador coincidente en logs. Cada sesión genera
`localstack/reports/laptop/<sesión>/karpenter.json`. `COMMAND_OK` solo confirma que
un comando terminó; únicamente la prueba completada devuelve `PASS` de runtime.
No equivale a API Gateway → PDF ni demuestra aislamiento IAM de LocalStack.

## Ejercicios

1. Ejecuta `load` y observa escalado desde cero; ejecuta `test` para evidencia.
2. Ejecuta `stop`: se detiene solo controlador. Verifica que pods/HTTP existentes
   continúan funcionando. `start` lo reanuda sin borrar nodos.
3. Para retirar carga y **borrar almacenamiento efímero de workers de práctica**:

   ```bash
   localstack/.venv/bin/python localstack/karpenter/karpenter.py scale-down --delete-data
   ```

   Comando rechaza workloads ajenos y PVCs en esos workers; escala práctica a cero,
   elimina NodeClaims uno por uno y exige desaparición real de contenedores Docker
   y MD en cero. Conserva control planes y LocalStack. No aprobar si falla.
4. Repite `load` y `test`: deben aparecer workers nuevos. Guarda IDs de ambos reportes.

Disrupción automática está deshabilitada mediante presupuesto cero; borrado
explícito prueba ruta NodeClaim → CAPI → Docker. No se promete consolidación,
reparación o comportamiento EC2 productivo del proveedor experimental.

Para detener controlador conservando datos:

```bash
localstack/.venv/bin/python localstack/karpenter/karpenter.py stop
```

No hay limpieza global Docker ni eliminación automática de clústeres. Conservar
`.state/karpenter-capd/` permite verificar propietarios y retomar comandos.

## Si una prueba falla

Revisa reporte y logs, sin borrar finalizers para obtener un resultado aparente.
`Pending` puede deberse a capacidad de laptop, descarga de imágenes, CNI, selector
o controlador detenido. `NodeClaim` sin Machine requiere revisar controlador CAPI;
Machine sin Node Ready requiere revisar CAPD/bootstrap y Docker. Inspección:

```bash
kubectl --kubeconfig "$LAB_CAPD_WORKLOAD" -n micro-test-local-practice get pods -o wide
kubectl --kubeconfig "$LAB_CAPD_WORKLOAD" -n micro-test-local-practice get events --sort-by=.lastTimestamp
kubectl --kubeconfig "$LAB_CAPD_MGMT" -n capd-system logs deployment/capd-controller-manager --tail=100
kubectl --kubeconfig "$LAB_CAPD_MGMT" -n micro-test-local-capd get machines,dockermachines -o wide
```

Validaciones offline disponibles:

```bash
localstack/.venv/bin/python localstack/karpenter/karpenter.py validate
localstack/.venv/bin/python -m unittest discover -s localstack/karpenter -p 'test_*.py' -v
```

Se comprobaron ocho recursos contra CRDs fijados y siete pruebas negativas de
guardias. No validan admission/CEL, compilación Go, Docker, CNI ni escalado.

Fuentes: [proveedor y matriz v0.2.0](https://github.com/kubernetes-sigs/karpenter-provider-cluster-api/blob/v0.2.0/docs/docs/getting-started.md),
[ejemplo CAPD v1.10.1](https://github.com/kubernetes-sigs/cluster-api/blob/v1.10.1/test/infrastructure/docker/examples/simple-cluster.yaml),
[bind CAPD parcheado](https://github.com/kubernetes-sigs/cluster-api/blob/v1.10.1/test/infrastructure/docker/internal/docker/loadbalancer.go),
[kubeconfig Docker Desktop](https://github.com/kubernetes-sigs/cluster-api/blob/v1.10.1/docs/book/src/clusterctl/developers.md#additional-notes-for-the-docker-provider),
[imagen kind por digest](https://github.com/kubernetes-sigs/kind/releases/tag/v0.29.0).
