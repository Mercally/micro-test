# Laboratorio local con LocalStack Student en Windows + WSL2

Guía para este repositorio. Revisión documental: **3 de noviembre de 2026**.
Los ejemplos no se han ejecutado en tu PC. Primero comprueba el arranque básico;
después incorpora Kubernetes y los servicios de integración.

## 1. Equipo recomendado

**Usar PC Intel i7 con 32 GB RAM como laboratorio principal.** No conocemos generación
ni cantidad de núcleos del i7: la recomendación se basa en RAM disponible y arquitectura
x86_64, no en afirmar que su CPU supera al M1.

| Equipo | Uso recomendado | Asignación inicial propuesta |
| --- | --- | --- |
| PC i7, 32 GB, Windows + WSL2 | LocalStack, EKS local, tres grupos de nodos, Istio y PostgreSQL | WSL hasta 20 GB; conservar margen para Windows |
| Mac M1, 16 GB | Micro + PostgreSQL + S3; pruebas serverless por separado | Runtime de contenedores con 6–8 GB; evitar todo el laboratorio simultáneamente |

Son presupuestos iniciales, no mínimos oficiales ni resultados de benchmark. Revisar
`docker stats`, memoria disponible y swap. Reservar al menos 60 GB libres en SSD para
imágenes, compilaciones y datos. Para cargas pesadas, cerrar navegadores y otros IDE.
En M1, preferir imágenes ARM64; ejecutar `linux/amd64` mediante emulación añade costo
de CPU/RAM. PC evita esa emulación al usar las imágenes amd64 del laboratorio AWS.

## 2. Qué se puede levantar hoy

El micro implementa seis pasos HTTP, PostgreSQL y S3. El Compose existente utiliza
`localstack/localstack:3.8` y `SERVICES=s3`: **no es configuración del laboratorio
Student completo**. También publica PostgreSQL en **5666**, aunque README menciona 5432.

El flujo objetivo vigente está en [cloudformation/README.md](cloudformation/README.md):

```text
API → micro-aceptación → PostgreSQL/outbox → Lambda publisher
    → SQS → EventBridge Pipe → Step Functions → microservicios
```

Outbox, publisher, authorizer y workflow de negocio todavía requieren implementación.
Esta guía permite comprobar componentes y contratos por separado. No convierte el
micro actual en consumidor SQS ni crea lógica de negocio que no existe.

No aplicar directamente [aws/kubernetes/demo.yaml](aws/kubernetes/demo.yaml), los
stacks antiguos de `aws/cloudformation/` ni `aws/sql/init.sql`: pertenecen a otro
diseño. La ASL existente en `statemachine/` es un dispatcher **Step Functions → SQS**;
no representa el flujo objetivo **SQS → Pipe → Step Functions**.

## 3. Preparar Windows, WSL y herramientas

En PowerShell, comprobar WSL y actualizarlo si corresponde:

```powershell
wsl --version
wsl --update
wsl --list --verbose
```

Ubuntu debe usar WSL **2**. En Docker Desktop habilitar motor WSL2 e integración con
esa distribución. Usar contenedores Linux. No instalar un segundo Docker Engine
dentro de Ubuntu si vas a utilizar la integración de Docker Desktop.

Editar `%UserProfile%\.wslconfig`, conservando otras opciones que ya necesites:

```ini
[wsl2]
memory=20GB
swap=4GB
```

No fijar `processors` sin conocer núcleos disponibles. Guardar trabajos antes de
ejecutar `wsl --shutdown`: detiene todas las distribuciones y sus procesos. Reiniciar
Docker Desktop y Ubuntu después del cambio.

Trabajar con una copia del repo bajo `~/code/micro-test` en filesystem Linux de WSL,
evitando `/mnt/c/...` para compilaciones y volúmenes. Abrir terminal Ubuntu ahí.

Instalar Docker Compose v2, AWS CLI v2, `curl`, `jq`, Python 3 y Git. Para fase EKS,
añadir `kubectl`, `k3d` e `istioctl`, siguiendo instaladores oficiales. Comprobar:

```bash
docker version
docker compose version
aws --version
jq --version
python3 --version
```

No hace falta Java/Maven en WSL para construir el micro: Dockerfile incluye ambos.
Descarga de imágenes, dependencias y activación de licencia requieren Internet;
ejecución local no significa entorno completamente desconectado.

Fuentes: [WSL y RAM](https://learn.microsoft.com/en-us/windows/wsl/wsl-config),
[Docker Desktop con WSL2](https://docs.docker.com/desktop/features/wsl/).

## 4. Activar Student y aislar archivos del laboratorio

Vincular cuenta LocalStack con **GitHub Student Developer Pack activo** y verificar
plan Student en Billing. Incluye EKS, ECR, RDS y EventBridge Pipes, además de servicios
básicos. Obtener Auth Token desde cuenta LocalStack; nunca guardarlo en Git.

Usaremos archivos generados fuera del repo. Desde su raíz, en **Bash de WSL**:

```bash
export LAB_REPO="$PWD"
export LAB_DIR="$HOME/.local/share/micro-test-localstack"
mkdir -p "$LAB_DIR"
chmod 700 "$LAB_DIR"
read -rsp 'LocalStack Auth Token: ' LOCALSTACK_AUTH_TOKEN
printf '\n'
export LOCALSTACK_AUTH_TOKEN

# Solo credenciales ficticias para APIs emuladas.
unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_SESSION_TOKEN AWS_SECURITY_TOKEN
unset AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1
export AWS_REGION=us-east-1
export AWS_EC2_METADATA_DISABLED=true
export AWS_ENDPOINT_URL=http://localhost:4566
export AWS_PAGER=''
function awslab() {
  aws --endpoint-url=http://localhost:4566 --region us-east-1 "$@"
}
```

Todos los comandos AWS de esta guía usan `awslab`: endpoint explícito local.
No activar AWS Replicator, proxy/passthrough ni montar `~/.aws` en contenedores.
`LOCALSTACK_AUTH_TOKEN` activa licencia; no es Access Key de una cuenta AWS.

Tomar imagen actual una vez y fijar digest para siguientes arranques:

```bash
docker pull localstack/localstack:latest
docker image inspect localstack/localstack:latest \
  --format '{{index .RepoDigests 0}}' > "$LAB_DIR/localstack-image.txt"
export LOCALSTACK_IMAGE="$(cat "$LAB_DIR/localstack-image.txt")"
```

`latest` solo sirve para esa selección inicial; conservar digest y registrar versión
real que aparezca en logs. No volver a resolverlo en cada arranque.

Fuentes: [Student](https://www.localstack.cloud/localstack-for-students),
[licencias y cobertura](https://docs.localstack.cloud/aws/licensing/),
[instalación actual](https://docs.localstack.cloud/aws/getting-started/installation/).

## 5. Crear Compose independiente y arrancar base

No ejecutar simultáneamente `docker compose up` del repo: compite por 4566 y 8080.
Si ya corre, detener ese proyecto primero, sin borrar sus volúmenes.

El ejemplo usa red Docker `172.29.240.0/24`. Verificar con `ip route` y
`docker network ls` que no colisione con LAN/VPN/otras redes. Si la cambias, actualizar
IPs de Compose y de los manifiestos posteriores.

```bash
cat > "$LAB_DIR/compose.yaml" <<'YAML'
services:
  localstack:
    image: ${LOCALSTACK_IMAGE:?Exportar digest de imagen}
    environment:
      LOCALSTACK_AUTH_TOKEN: ${LOCALSTACK_AUTH_TOKEN:?Exportar token}
      PERSISTENCE: "1"
      ENFORCE_IAM: "0"
    ports:
      - "127.0.0.1:4566:4566"
      - "127.0.0.1:4510-4559:4510-4559"
    volumes:
      - localstack-data:/var/lib/localstack
      - /var/run/docker.sock:/var/run/docker.sock
    networks:
      lab:
        ipv4_address: 172.29.240.2
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: docs
      POSTGRES_USER: docs
      POSTGRES_PASSWORD: docs
    ports:
      - "127.0.0.1:5666:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    networks:
      lab:
        ipv4_address: 172.29.240.3
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U docs -d docs"]
      interval: 5s
      timeout: 3s
      retries: 30
  docs:
    image: docs-simulator:local
    environment:
      DATABASE_URL: jdbc:postgresql://postgres:5432/docs
      DATABASE_USERNAME: docs
      DATABASE_PASSWORD: docs
      AWS_REGION: us-east-1
      AWS_ACCESS_KEY_ID: test
      AWS_SECRET_ACCESS_KEY: test
      S3_ENDPOINT_URL: http://localstack:4566
      S3_PRESIGN_ENDPOINT_URL: http://localhost:4566
      S3_BUCKET: docs-simulator
      SIMULATOR_FAILURE_PROBABILITY: "0"
      SIMULATOR_FAILURES_BEFORE_SUCCESS: "0"
      OTEL_SDK_DISABLED: "true"
      JAVA_TOOL_OPTIONS: -XX:MaxRAMPercentage=65
    mem_limit: 768m
    ports:
      - "127.0.0.1:8080:8080"
    networks: [lab]
    depends_on:
      postgres:
        condition: service_healthy
volumes:
  localstack-data:
  postgres-data:
networks:
  lab:
    name: micro-test-lab
    ipam:
      config:
        - subnet: 172.29.240.0/24
YAML

function labcompose() {
  docker compose -p micro-test-lab -f "$LAB_DIR/compose.yaml" "$@"
}
labcompose up -d localstack postgres
labcompose logs --tail=60 localstack
curl -fsS http://localhost:4566/_localstack/health | jq
awslab sts get-caller-identity
awslab s3api create-bucket --bucket docs-simulator
docker build -t docs-simulator:local "$LAB_REPO"
labcompose up -d docs
curl -fsS http://localhost:8080/actuator/health | jq
```

Esperar a que LocalStack esté listo antes de crear bucket; si aún falla conexión,
revisar logs y repetir comprobación. En arranques posteriores, usar `head-bucket`;
no recrear recursos ya existentes. Esperar arranque Spring antes del health check.

Credenciales `docs/docs` y `test/test` son desechables, solo para esta red local.
Socket Docker da al emulador control del daemon: usar este laboratorio con código e
imágenes de confianza. No exponer 4566, DB ni el daemon en LAN/Internet.

**Salida de esta fase:** health `UP`, bucket visible y PostgreSQL disponible.
Motor PostgreSQL corre directamente, no a través de API RDS: reduce complejidad
inicial. Para practicar `CreateDBInstance`, ver fase de ampliación.

## 6. Probar flujo HTTP completo del micro

Todos los pasos usan la misma instancia/base de datos. El ejemplo requiere `jq`.

```bash
function postlab() {
  curl -fsS "http://localhost:8080/api/v1/$1" \
    -H 'Content-Type: application/json' -d "$2"
}
SALE_JSON=$(postlab endpoint0/sales \
  "$(jq -nc --arg ref "local-$(date +%s)" \
    '{externalReference:$ref,customerName:"Cliente Demo",customerEmail:"cliente@example.com",productCode:"AUTO-01"}')")
SALE_ID=$(jq -er '.saleId' <<< "$SALE_JSON")
DOC_JSON=$(postlab endpoint1/documents "$(jq -nc --arg id "$SALE_ID" '{saleId:$id}')")
DOC_ID=$(jq -er '.documentId' <<< "$DOC_JSON")
POL_JSON=$(postlab endpoint2/policies "$(jq -nc --arg id "$SALE_ID" '{saleId:$id}')")
POL_ID=$(jq -er '.policyId' <<< "$POL_JSON")
postlab endpoint3/documents/upload \
  "$(jq -nc --arg s "$SALE_ID" --arg d "$DOC_ID" '{saleId:$s,documentId:$d}')"
postlab endpoint4/policies/complete \
  "$(jq -nc --arg s "$SALE_ID" --arg p "$POL_ID" '{saleId:$s,policyId:$p}')"
postlab endpoint5/documents/release \
  "$(jq -nc --arg s "$SALE_ID" --arg d "$DOC_ID" '{saleId:$s,documentId:$d}')"
awslab s3 ls s3://docs-simulator/ --recursive
```

Comprobar HTTP 2xx, objeto PDF y URL de descarga de la última respuesta. Abrir URL
desde la misma PC; no sustituir su hostname después de firmarla. Esta prueba sigue
siendo HTTP manual: no prueba orquestación asíncrona.

## 7. Añadir EKS local y tres node groups

Mantener PostgreSQL y LocalStack. Parar micro de Compose para liberar memoria/8080:

```bash
labcompose stop docs
export KUBECONFIG="$LAB_DIR/kubeconfig"
VPC_ID=$(awslab ec2 create-vpc --cidr-block 10.20.0.0/16 --query Vpc.VpcId --output text)
SUBNET_A=$(awslab ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.20.11.0/24 --availability-zone us-east-1a --query Subnet.SubnetId --output text)
SUBNET_B=$(awslab ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block 10.20.12.0/24 --availability-zone us-east-1b --query Subnet.SubnetId --output text)
awslab iam create-role --role-name lab-eks --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"eks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
awslab iam create-role --role-name lab-node --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
awslab eks create-cluster --name micro-test-local \
  --role-arn arn:aws:iam::000000000000:role/lab-eks \
  --resources-vpc-config "subnetIds=$SUBNET_A,$SUBNET_B"
awslab eks wait cluster-active --name micro-test-local
for role in general terceros onprem; do
  awslab eks create-nodegroup --cluster-name micro-test-local \
    --nodegroup-name "$role" --node-role arn:aws:iam::000000000000:role/lab-node \
    --subnets "$SUBNET_A" "$SUBNET_B" \
    --scaling-config minSize=1,maxSize=1,desiredSize=1 --labels "lab-role=$role"
  awslab eks wait nodegroup-active --cluster-name micro-test-local --nodegroup-name "$role"
done
```

Este bootstrap usa IAM permisivo, no roles productivos completos. EKS se apoya en
k3d/k3s. Los dos nombres de AZ son metadatos locales, no alta disponibilidad física.
No habilitar Kubernetes incorporado de Docker Desktop: este flujo crea su propio clúster.

Identificar nombre **real** del clúster generado, distinto posiblemente de nombre EKS:

```bash
k3d cluster list
read -rp 'Nombre k3d del clúster de este laboratorio: ' K3D_CLUSTER
export K3D_CLUSTER
k3d kubeconfig get "$K3D_CLUSTER" > "$KUBECONFIG"
chmod 600 "$KUBECONFIG"
kubectl config current-context
kubectl get nodes -L lab-role
kubectl wait --for=condition=Ready nodes --all --timeout=180s
```

Debe haber workers con los tres valores `lab-role`. Si API declara node group activo
pero no aparece worker listo, detenerse y revisar logs/versiones: no tomar CRUD como
prueba de capacidad real. Guardar variables/IDs **sin token** para reinicio y limpieza.

El kubeconfig de k3d es acceso administrativo local independiente de EKS IAM. No prueba
Access Entries ni Pod Identity. Si el endpoint que genera no es accesible desde WSL,
revisar puertos publicados y documentación EKS de versión elegida; no desactivar TLS.

Fuente: [EKS local y Managed Node Groups](https://docs.localstack.cloud/aws/services/eks/).

## 8. Conectar Kubernetes con DB/S3 y desplegar micro

Conectar contenedores de nodos k3d a red del laboratorio. Primero inspeccionar lista;
debe contener solo nodos del clúster que acabas de crear:

```bash
docker ps --filter "label=k3d.cluster=$K3D_CLUSTER" --format '{{.Names}}'
for node in $(docker ps --filter "label=k3d.cluster=$K3D_CLUSTER" --format '{{.Names}}'); do
  if ! docker network inspect micro-test-lab --format '{{range .Containers}}{{println .Name}}{{end}}' | grep -Fxq "$node"; then
    docker network connect micro-test-lab "$node"
  fi
done
k3d image import docs-simulator:local -c "$K3D_CLUSTER"
kubectl create namespace general
kubectl -n general run connectivity --image=busybox:1.37 --restart=Never \
  --command -- sh -c 'nc -z -w 5 172.29.240.3 5432 && wget -qO- http://172.29.240.2:4566/_localstack/health'
kubectl -n general logs -f connectivity
```

Ambas conexiones deben funcionar; no desplegar aplicación si esta comprobación falla.
La unión a red Docker es adaptación local, no implementación de subredes o SG AWS.

```bash
kubectl -n general create secret generic database-credentials \
  --from-literal=username=docs --from-literal=password=docs
cat > "$LAB_DIR/micro.yaml" <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: docs
  namespace: general
spec:
  replicas: 1
  selector:
    matchLabels: {app: docs}
  template:
    metadata:
      labels: {app: docs}
    spec:
      nodeSelector: {lab-role: general}
      containers:
        - name: docs
          image: docs-simulator:local
          imagePullPolicy: Never
          ports: [{containerPort: 8080}]
          resources:
            requests: {cpu: 250m, memory: 384Mi}
            limits: {cpu: "1", memory: 768Mi}
          env:
            - {name: DATABASE_URL, value: "jdbc:postgresql://172.29.240.3:5432/docs"}
            - name: DATABASE_USERNAME
              valueFrom: {secretKeyRef: {name: database-credentials, key: username}}
            - name: DATABASE_PASSWORD
              valueFrom: {secretKeyRef: {name: database-credentials, key: password}}
            - {name: AWS_REGION, value: us-east-1}
            - {name: AWS_ACCESS_KEY_ID, value: test}
            - {name: AWS_SECRET_ACCESS_KEY, value: test}
            - {name: S3_BUCKET, value: docs-simulator}
            - {name: S3_ENDPOINT_URL, value: "http://172.29.240.2:4566"}
            - {name: S3_PRESIGN_ENDPOINT_URL, value: "http://localhost:4566"}
            - {name: SIMULATOR_FAILURE_PROBABILITY, value: "0"}
            - {name: OTEL_SDK_DISABLED, value: "true"}
            - {name: JAVA_TOOL_OPTIONS, value: "-XX:MaxRAMPercentage=65"}
          readinessProbe:
            httpGet: {path: /actuator/health/readiness, port: 8080}
            initialDelaySeconds: 15
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata: {name: docs, namespace: general}
spec:
  selector: {app: docs}
  ports: [{port: 8080, targetPort: 8080}]
YAML
kubectl apply -f "$LAB_DIR/micro.yaml"
kubectl -n general rollout status deployment/docs --timeout=180s
kubectl -n general port-forward service/docs 8080:8080
```

Dejar port-forward en esa terminal; repetir prueba HTTP de sección 6 desde otra.
Por ahora solo se despliega micro en `general`. Los tres node groups existen para
practicar scheduling; antes de desplegar roles adicionales, definir qué datos y APIs
deben compartir. No repartir los seis pasos entre bases distintas sin ese contrato.

## 9. Instalar Istio y comprobar mTLS

Elegir versión de Istio compatible con Kubernetes mostrado por `kubectl version`.
Instalar `istioctl` de esa versión y registrar número. Este ejemplo usa sidecars:

```bash
istioctl version --remote=false
istioctl install --set profile=minimal -y
kubectl label namespace general istio-injection=enabled --overwrite
kubectl -n general delete pod connectivity
kubectl -n general rollout restart deployment/docs
kubectl -n general rollout status deployment/docs --timeout=180s
kubectl -n general get pods
cat > "$LAB_DIR/mtls.yaml" <<'YAML'
apiVersion: security.istio.io/v1
kind: PeerAuthentication
metadata: {name: default, namespace: general}
spec:
  mtls: {mode: STRICT}
YAML
kubectl apply -f "$LAB_DIR/mtls.yaml"
istioctl analyze -n general
```

Esperar contenedores de aplicación y proxy listos. La instalación requiere permisos
de administrador del clúster; algunas configuraciones Pod Security necesitan ajustes
de instalación de Istio. No copiar `restricted` del diseño AWS sin verificar bootstrap.

**Prueba de seguridad:** desde un cliente con sidecar, acceder a
`http://docs.general.svc.cluster.local:8080/actuator/health`. Repetir desde cliente sin
sidecar en otro namespace: debe fallar por mTLS. `port-forward` sirve para depurar y
no demuestra política entre pods. Agregar `AuthorizationPolicy` para permisos por
identidad; mTLS solo no define quién puede llamar cada API.

Para aislamiento de red, aplicar NetworkPolicy con CNI que realmente la haga cumplir.
Comprobar DNS, tráfico permitido y prohibido antes/después. No asumir que labels,
node groups o metadatos SG aíslan tráfico. k3s tiene componentes de red propios;
no instalar otro CNI encima sin plan de sustitución y compatibilidad.

Fuente: [Istio sobre k3d](https://istio.io/latest/docs/setup/platform-setup/k3d/).

## 10. Añadir servicios sin fingir que negocio ya está integrado

### Prueba mínima SQS → Pipe → Step Functions

Esta prueba crea recursos nuevos de diagnóstico y confirma cableado asíncrono. La
máquina termina con `Pass`: no invoca micros ni confirma procesamiento de una venta.
Ejecutar una vez, con IAM permisivo del bootstrap. En esta prueba no hay otro
consumidor leyendo la cola.

```bash
awslab iam create-role --role-name lab-states --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"states.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
awslab iam create-role --role-name lab-pipe --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"pipes.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
QUEUE_URL=$(awslab sqs create-queue --queue-name lab-diagnostic \
  --query QueueUrl --output text)
QUEUE_ARN=$(awslab sqs get-queue-attributes --queue-url "$QUEUE_URL" \
  --attribute-names QueueArn --query Attributes.QueueArn --output text)
STATE_ARN=$(awslab stepfunctions create-state-machine --name lab-diagnostic \
  --role-arn arn:aws:iam::000000000000:role/lab-states --type STANDARD \
  --definition '{"StartAt":"Recibido","States":{"Recibido":{"Type":"Pass","End":true}}}' \
  --query stateMachineArn --output text)
awslab iam put-role-policy --role-name lab-pipe --policy-name consume-and-start \
  --policy-document "$(jq -nc --arg q "$QUEUE_ARN" --arg s "$STATE_ARN" \
    '{Version:"2012-10-17",Statement:[{Effect:"Allow",Action:["sqs:ReceiveMessage","sqs:DeleteMessage","sqs:GetQueueAttributes"],Resource:$q},{Effect:"Allow",Action:"states:StartExecution",Resource:$s}]}')"
awslab pipes create-pipe --name lab-diagnostic \
  --role-arn arn:aws:iam::000000000000:role/lab-pipe \
  --source "$QUEUE_ARN" --target "$STATE_ARN" --desired-state STOPPED \
  --source-parameters '{"SqsQueueParameters":{"BatchSize":1}}' \
  --target-parameters '{"StepFunctionStateMachineParameters":{"InvocationType":"FIRE_AND_FORGET"}}'
awslab pipes start-pipe --name lab-diagnostic
awslab pipes describe-pipe --name lab-diagnostic --query CurrentState
```

Esperar estado `RUNNING` antes del siguiente bloque. El body SQS es un string JSON
contenido en un evento de lote: comprobar estructura de entrada real antes de
diseñar transformaciones para el workflow de negocio.

```bash
awslab sqs send-message --queue-url "$QUEUE_URL" \
  --message-body '{"diagnosticId":"local-001","saleId":"solo-prueba"}'
awslab stepfunctions list-executions --state-machine-arn "$STATE_ARN"
```

Repetir `list-executions` hasta ver ejecución. Copiar su ARN y consultar
`awslab stepfunctions describe-execution --execution-arn ARN_LOCAL` y
`awslab stepfunctions get-execution-history --execution-arn ARN_LOCAL`.
Debe terminar `SUCCEEDED` y conservar mensaje esperado en `input`. Si no ocurre,
revisar Pipe, cola y logs; no sustituirlo por `start-execution` manual y contar eso
como prueba del Pipe.

### Ampliaciones del laboratorio

| Componente | Siguiente paso concreto | Prueba necesaria |
| --- | --- | --- |
| ECR | `awslab ecr create-repository --repository-name micro-test/docs`; consultar `repositoryUri` y publicar imagen con login local | Push y pull por digest; comprobar que hostname es local antes de autenticar Docker |
| EC2 | Crear instancia desde una AMI admitida por versión local; revisar backend Docker | Proceso real, UserData/IMDS y recursos; contenedor no es VM Nitro |
| RDS | Crear DB PostgreSQL vía `awslab rds create-db-instance`, con nombre/usuario propios; leer Endpoint.Address y Port devueltos | Conexión SQL real; sustituir URL JDBC solo después de probar alcance desde pod |
| SQS/DLQ | Crear ambas colas y configurar redrive, visibility timeout y maxReceiveCount | Mensaje no borrado reaparece y termina en DLQ después del límite |
| Step Functions | Crear máquina diagnóstica `Pass` para prueba de infraestructura; luego escribir ASL del flujo actual | Estado final e historial correctos; `Pass` no procesa venta |
| EventBridge Pipes | Crear Pipe SQS → máquina anterior, rol con Receive/Delete/GetQueueAttributes y states:StartExecution; empezar STOPPED | Arrancar y enviar mensaje; ejecución recibe payload esperado; detener antes de cambiar targets |
| Lambda | Empaquetar función diagnóstica, crear rol y usar create-function/invoke locales | Runtime ejecuta y escribe logs; después implementar publisher real con outbox/idempotencia |
| API Gateway | Elegir REST o HTTP y crear integración con target alcanzable; authorizer requiere contrato propio | HTTP real llega al target; validar payload, token inválido y expiración |
| Secrets Manager/KMS | Crear secreto/clave y permisos de identidad de prueba | Lectura/cifrado autorizado y Deny cruzado |
| Observabilidad | Empezar con logs de LocalStack y kubectl; habilitar exportador OTel después | Traza correlacionada, sin endpoint remoto accidental |

Sección 8 carga imagen directamente con k3d: **no prueba pull desde ECR ni permisos
del kubelet**. Para esa prueba adicional, configurar registry/credenciales de nodos
según versión elegida y usar URI ECR devuelta, no ARN/host de tu ECR real.

RDS local tampoco certifica seguridad de RDS: documentación actual indica que
`rds.force_ssl` no obliga TLS y que `verify-full` no está soportado. No contar como
éxito una prueba de rechazo sin TLS que el emulador no implementa.

Manuales oficiales de ampliación:
[ECR](https://docs.localstack.cloud/aws/services/ecr/),
[EC2](https://docs.localstack.cloud/aws/services/ec2/),
[RDS](https://docs.localstack.cloud/aws/services/rds/),
[Lambda](https://docs.localstack.cloud/aws/services/lambda/),
[SQS](https://docs.localstack.cloud/aws/services/sqs/),
[Step Functions](https://docs.localstack.cloud/aws/services/stepfunctions/),
[Pipes](https://docs.localstack.cloud/aws/services/pipes/),
[API Gateway](https://docs.localstack.cloud/aws/services/apigateway/).

## 11. IAM y límites de redes

Bootstrap inicia con `ENFORCE_IAM=0`. Para probar autorización, cambiar a `"1"` en
Compose y recrear LocalStack **en una sesión dedicada**, después de guardar trabajo
y detener workloads. Crear identidades locales específicas, reemplazar `test/test`
en clientes de prueba y conceder acciones mínimas. No afirmar aislamiento IAM usando
credenciales administrativas de bootstrap. Revisar impacto de reiniciar emulador sobre
contenedores EKS y otros motores antes de reutilizar esta sesión.

Pruebas mínimas: usuario sin políticas recibe AccessDenied; con Allow para un bucket,
accede solo a ese bucket; un Deny explícito vuelve a bloquearlo. Comprobar también
roles de Lambda/Pipes y credenciales de pods cuando se incorpore Pod Identity.

VPC/SG/ENI/route tables se pueden crear por API, pero su presencia no demuestra
routing, firewall o PrivateLink equivalentes a AWS. Este laboratorio conecta DB/S3
por red Docker deliberadamente: no prueba SG de RDS, NACL, VPC endpoints ni
condiciones `aws:SourceVpce`. Reservar esas pruebas para AWS real o un laboratorio
de red virtual diseñado para demostrar paquetes permitidos/denegados.

Fuente: [IAM enforcement](https://docs.localstack.cloud/aws/developer-tools/security-testing/iam-policy-enforcement/).

## 12. Reinicio, limpieza y fallos comunes

Para volver a entrar, exportar `LAB_DIR`, `LAB_REPO`, digest guardado y token como en
sección 4; redefinir `awslab` y `labcompose`, y apuntar `KUBECONFIG` al archivo local.
No repetir `create-*` sobre recursos que ya existen. Persistencia del emulador no
garantiza restauración completa de clúster/motores tras apagar Docker o Windows:
comprobar nodos, DB, credenciales y pods antes de continuar.

| Síntoma | Revisar |
| --- | --- |
| EKS/Pipes no disponibles | Plan Student activo, token asignado e imagen actual; no Compose antiguo 3.8 |
| 4566/8080 ocupados | Otro proyecto Compose o port-forward anterior |
| Reinicios/OOMKilled | `docker stats`, memoria WSL y límites de pods; reducir laboratorio |
| Pod no alcanza S3/DB | Red `micro-test-lab`, IPs y prueba connectivity; localhost en pod es el mismo pod |
| ImagePullBackOff | Imagen importada en clúster correcto; nombre/tag y política de pull |
| Pod Pending | Etiqueta lab-role presente, worker Ready, recursos/taints compatibles |
| URL firmada no abre | Puerto accesible en PC y hostname configurado antes de firmar |
| EKS activo pero kubectl falla | Contexto aislado, endpoint/puerto k3d, estado Docker y workers |
| DNS/licencia fallan en WSL | Proxy/VPN, DNS y acceso HTTPS a LocalStack; comprobar logs sin divulgar token |

Para apagar laboratorio, primero eliminar node groups y EKS **desde API local**:

```bash
for role in general terceros onprem; do
  awslab eks delete-nodegroup --cluster-name micro-test-local --nodegroup-name "$role"
  awslab eks wait nodegroup-deleted --cluster-name micro-test-local --nodegroup-name "$role"
done
awslab eks delete-cluster --name micro-test-local
awslab eks wait cluster-deleted --name micro-test-local
labcompose down
```

Si conectaste nodos a red Docker y un fallo dejó contenedores huérfanos, inspeccionar
`k3d cluster list` y contenedores antes de borrar exclusivamente el clúster local
identificado. Una red con endpoints huérfanos puede impedir `compose down`.
No usar `docker system prune` para resolverlo.

`down` conserva volúmenes. **`labcompose down -v` borra datos locales de PostgreSQL y
LocalStack**: usarlo solo para reiniciar laboratorio desde cero tras conservar lo
necesario. VPC/subredes/roles y recursos adicionales persisten hasta eliminarlos por
API o borrar estado. Token sigue en entorno de shell; al terminar:

```bash
unset LOCALSTACK_AUTH_TOKEN
```

## 13. Checklist de aceptación

- [ ] Health del micro `UP` y flujo HTTP completo con PDF en S3.
- [ ] EKS local accesible; tres workers con roles esperados y pods Ready.
- [ ] DB/S3 alcanzables desde pod; endpoint local explícito.
- [ ] Istio instalado; cliente con mTLS funciona y cliente sin mTLS falla.
- [ ] NetworkPolicy comprobada con prueba permitida y otra denegada.
- [ ] ECR push/pull comprobado si se incorpora; import directo no cuenta.
- [ ] Lambda, SQS/DLQ, Pipes y state machine probados individualmente antes del flujo real.
- [ ] Outbox/publisher/workflow de negocio identificados como pendientes hasta implementarlos.
- [ ] IAM probado con identidades restringidas y enforcement activado.
- [ ] Digest/versiones y procedimiento de recreación registrados, sin secretos.
- [ ] Ninguna llamada a ECR/AWS real, replicator o passthrough durante pruebas locales.

**Estado de esta guía:** revisada contra configuración del repo y documentación
oficial. Comandos preparados para ejecución manual en PC/WSL2; no validados mediante
despliegue real en tu equipo.
