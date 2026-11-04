# Desplegar personalmente en laptop: localstack-laptop

**Actualización 2026-11-04:** los diez stacks base ya están desplegados. Se añadieron
artefactos y una Lambda de comprobación SQL; RDS responde desde Lambda, pero aún no
tiene tablas `sales`/`outbox`. Para completar compras y enviar `.http`, seguir
[business/RUNBOOK.md](business/RUNBOOK.md). No hace falta repetir el despliegue base.
Esta guía conserva abajo la lectura inicial del 2026-11-03.

Destino comprobado el 2026-11-03:

- Perfil AWS CLI: `localstack-laptop`, región `us-east-1`, credenciales ficticias `test/test`.
- Gateway desde Mac: `http://100.100.154.68:4566`.
- Gateway dentro de la laptop: `http://127.0.0.1:4566`.
- Host reportado: Docker Desktop sobre Windows/WSL2, x86_64.
- LocalStack `2026.10.4:a39e30ef`, edición Pro, licencia activa.
- Persistencia reportada: `disabled`.
- Los once nombres de stack consultados están `ABSENT`; todavía no se desplegó
  infraestructura de este repositorio en la laptop.

El emulador ya está levantado. Esta guía despliega los recursos y la aplicación.
No hace falta iniciar otro LocalStack ni ejecutar el Compose raíz del repositorio.
Cambiar perfil no copia recursos desde la PC anterior: cada gateway tiene su estado.
Las pruebas registradas en `VALIDATION.md` corresponden al host anterior.

El host nuevo se añadió deliberadamente a la lista permitida de los scripts. No
se autoriza cualquier IP Tailscale. `lab.py` no tiene argumento `--profile`: utiliza
endpoint explícito y sus propias credenciales ficticias. El perfil sirve para tus
comandos AWS CLI; ambos métodos apuntan al mismo emulador y cuenta local.

## 1. Desde Mac: preparar comandos

Desde la raíz del repositorio:

```bash
python3 -m venv localstack/.venv
localstack/.venv/bin/pip install -r localstack/requirements.txt
localstack/.venv/bin/python localstack/scripts/test_safety.py

export AWS_PROFILE=localstack-laptop
export LAB_ENDPOINT=http://100.100.154.68:4566
function lab() {
  localstack/.venv/bin/python localstack/scripts/lab.py --endpoint "$LAB_ENDPOINT" "$@"
}

# Consulta configuración local, sin llamar a AWS.
aws configure get endpoint_url --profile localstack-laptop
lab status
lab validate
lab probe
```

El endpoint del perfil debe coincidir con `LAB_ENDPOINT`. Los scripts comprueban
identidad LocalStack y cuenta `000000000000`, ignoran sesiones/roles reales y
rechazan cualquier salida del SDK fuera del gateway. Para una consulta CLI manual,
mantener perfil, endpoint y región explícitos:

```bash
aws --profile localstack-laptop --endpoint-url "$LAB_ENDPOINT" --region us-east-1 \
  cloudformation describe-stacks --query 'Stacks[].[StackName,StackStatus]' --output table
```

Antes de conservar datos de práctica, prepara persistencia en la configuración de
arranque de **esta laptop**: `PERSISTENCE=1` y volumen durable en
`/var/lib/localstack`. La configuración detectada todavía la desactiva. No se
modificó ni reinició el contenedor; preservar sus datos existentes antes de cambiar
el arranque. Un perfil AWS no activa persistencia ni licencia.

## 2. Desde Mac: infraestructura y diagnóstico

```bash
lab deploy --only network registry database storage queues lambda workflow pipe api
lab status
lab smoke
localstack/.venv/bin/python localstack/scripts/checks.py --endpoint "$LAB_ENDPOINT" queues
```

Resultado esperado: API Gateway → Lambda y SQS → Pipe → Step Functions → Lambda
funcionan con payload correlacionado. El Pipe vuelve a `STOPPED`. Esto prueba
integración de infraestructura; el flujo asíncrono de ventas todavía no existe.

Después, crear EKS y sus tres grupos:

```bash
lab deploy --only eks
localstack/.venv/bin/python localstack/scripts/audit.py --endpoint "$LAB_ENDPOINT"
```

Estos comandos son ejecutables desde Mac porque usan 4566. Los contenedores EKS
se crean en Docker de la laptop. Vigila memoria allí; no presupone que la laptop
tenga los 32 GB de la PC anterior. Un estado ACTIVE no demuestra workers Ready.

Los scripts reconocen stacks propios sin cambios. Si hay una plantilla distinta,
fallan para revisar el cambio; no actualizan/reemplazan automáticamente bases o
clústeres. Los workarounds de Pipes son los documentados en `README.md`: crear con
STOPPED, reafirmar mediante UpdatePipe y usar UpdatePipe para transiciones.

## 3. En WSL de la laptop: micro, Kubernetes e Istio

Esta fase se ejecuta en **Ubuntu/WSL de la laptop**, no en la terminal Mac.
Publicar 4566 por Tailscale no proporciona Docker, registry, PostgreSQL o acceso
Kubernetes desde Mac.

Copia esta versión del repositorio a `~/code/micro-test`, incluyendo `localstack/`
y excluyendo `.venv/`, `.state/` y `__pycache__/`. La carpeta `localstack/` todavía
puede estar sin commit: un clon del remoto no incluirá cambios locales sin publicar.
No copies un virtualenv macOS a Linux. Instala en WSL Docker CLI con integración
Docker Desktop, kubectl, k3d e istioctl **1.31.3**; no otro Docker Engine.
La instalación fijada de istioctl está descrita en `README.md`, sección Bootstrap.

```bash
cd ~/code/micro-test
python3 -m venv localstack/.venv
localstack/.venv/bin/pip install -r localstack/requirements.txt

export LAB_ENDPOINT=http://127.0.0.1:4566
function lab() {
  localstack/.venv/bin/python localstack/scripts/lab.py --endpoint "$LAB_ENDPOINT" "$@"
}
lab status
docker version
kubectl version --client
k3d cluster list
istioctl version --remote=false
docker ps --format '{{.Names}} {{.Image}}'

read -rp 'Clúster k3d micro-test-local-eks... observado: ' LAB_K3D_CLUSTER
read -rp 'Contenedor LocalStack de esta laptop: ' LAB_LOCALSTACK_CONTAINER
docker inspect "$LAB_LOCALSTACK_CONTAINER" --format '{{json .NetworkSettings.Networks}}'
read -rp 'Nombre de su red Docker observado arriba: ' LAB_DOCKER_NETWORK

localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint "$LAB_ENDPOINT" --k3d-cluster "$LAB_K3D_CLUSTER" bootstrap \
  --localstack-container "$LAB_LOCALSTACK_CONTAINER" --docker-network "$LAB_DOCKER_NETWORK" \
  --database rds --presign-endpoint http://100.100.154.68:4566
```

Bootstrap construye el micro existente, importa imagen, instala Istio, consulta
SQL desde el clúster, despliega una instancia del micro y prueba scheduling,
mTLS, autorización y NetworkPolicy. Los seis pasos usan una misma base.

Si falla, revisar el error y no continuar como si hubiese terminado. PostgreSQL
en StatefulSet está disponible mediante `--database postgres` como alternativa
explícita a RDS: crea otra base; no migra ventas ni demuestra RDS.

El bloque siguiente conserva la **prueba manual anterior sin autenticación**.
Para compras nuevas, sustituirlo por `business/wsl.py` de
[business/RUNBOOK.md](business/RUNBOOK.md); no desplegar `micro-api` junto a negocio.
Para ejecutar únicamente aquel diagnóstico, cuando bootstrap termine:

```bash
lab deploy --only micro-api --parameters localstack/.state/micro-api-parameters.json

# Prueba adicional de ECR; importar con k3d no prueba push/pull.
localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint "$LAB_ENDPOINT" --k3d-cluster "$LAB_K3D_CLUSTER" registry
```

El API del micro usa dirección Docker del gateway obtenida por bootstrap. No
copiar IP de la PC anterior ni reemplazarla por una IP LAN. RDS y Kubernetes
tampoco se vuelven accesibles desde Mac por cambiar el perfil CLI.

## 4. Desde Mac: probar HTTP y PDF

En la terminal Mac, mantener `LAB_ENDPOINT=http://100.100.154.68:4566`:

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

Comprueba seis pasos HTTP, orden inválido, idempotencia de referencia, PDF en S3
y descarga prefirmada con el hostname nuevo. Aún es un recorrido HTTP de prueba,
no aceptación asíncrona por outbox.

## 5. Permisos, persistencia y parada

```bash
localstack/.venv/bin/python localstack/scripts/checks.py --endpoint "$LAB_ENDPOINT" iam
```

Exige enforcement efectivo e identidad restringida. Bootstrap usa credenciales
administrativas ficticias; no demuestra aislamiento IAM. La prueba histórica
falló en la PC anterior; volver a medir en laptop, sin copiar ese resultado.

Para practicar reinicios, preparar primero persistencia durable, conservar
`.state/http-proof.json`, reiniciar únicamente el laboratorio y ejecutar
`checks.py restore --micro-url "$LAB_MICRO_URL"` con endpoint explícito. No
regenerar datos antes de comprobar recuperación.

Parar workloads desde WSL:

```bash
localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint http://127.0.0.1:4566 --k3d-cluster "$LAB_K3D_CLUSTER" stop
```

Desde Mac, con endpoint de **laptop**:

```bash
lab cleanup
```

Conserva datos, EKS y dependencias del Pipe detenido. Eliminación irreversible:
`lab cleanup --delete-data`. No usar el endpoint antiguo para limpiar laptop ni
ejecutar limpieza global de Docker. Alcance exacto en `README.md`.

## Completar el flujo de ventas

Usar [PROMPT_LOCAL_ARCHITECTURE.md](PROMPT_LOCAL_ARCHITECTURE.md) en una nueva sesión
con acceso al repo y, para validación completa, una terminal de WSL de la laptop.
El prompt autoriza cambios mínimos de aplicación necesarios para outbox y flujo
real; conserva la separación de infraestructura AWS real.

Referencia: [precedencia de endpoints AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-endpoints.html).
`--endpoint-url` tiene prioridad sobre la configuración del perfil; por eso se
mantiene explícito incluso al usar `localstack-laptop`.
