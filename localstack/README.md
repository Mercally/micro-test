# LocalStack: punto único de arranque

**Entrada:** `./localstack/lab`. Destino: Windows/WSL2 x86_64 con Docker Linux.
AWS real queda fuera de estos comandos. Diseño siguiente: [AWS](../aws/README.md).

## Ciclo de laboratorio descartable

Ejecutar dentro de Ubuntu/WSL, desde el checkout Linux (`~/source/micro-test`):

```bash
./localstack/lab down --delete-data
./localstack/lab up
```

Para clientes de la laptop remota ya accesible por Tailscale, usa
`./localstack/lab up --client-endpoint http://100.100.154.68:4566`; genera URLs
API/PDF para ese destino. Esta opción no publica puertos ni configura Tailscale.

`down` pausa admisión, espera workflows, inventaría recursos propios y elimina
contenedores, redes y volúmenes del lab, incluyendo workers y helpers. Borra
`.state` para no reutilizar IPs, kubeconfig ni recibos de infraestructura vieja.
Conserva código, venv, reportes, imágenes compartidas y recursos de otros proyectos.
El plan/recibo queda en `~/.local/state/micro-test/cleanup*.json`; repetir `down`
puede completar una limpieza interrumpida. Helpers sin propiedad demostrada
detienen la operación para revisión mediante `cleanup`.

**Este ciclo pierde todas las compras, SQL y PDFs.** `up` reconstruye y descubre
IPs/puertos actuales; éxito exige nodo Karpenter real y pruebas de compra/PDF.
No equivale a reiniciar Docker Desktop y recuperar el estado persistido. Para
pausar únicamente procesamiento usa `pause`/`resume`.

`up` acepta `LOCALSTACK_AUTH_TOKEN` o lee `~/.config/micro-test/localstack-token`,
archivo privado del usuario con permisos `600`, fuera del repo. Para guardarlo
por primera vez, introduce token mediante `read -rsp` como se muestra abajo;
después, en otro bloque:

```bash
mkdir -p "$HOME/.config/micro-test"
chmod 700 "$HOME/.config/micro-test"
(umask 077; printf '%s' "$LOCALSTACK_AUTH_TOKEN" > "$HOME/.config/micro-test/localstack-token")
chmod 600 "$HOME/.config/micro-test/localstack-token"
unset LOCALSTACK_AUTH_TOKEN
./localstack/lab up
```

No guardes token en Git ni lo pegues en argumentos de comandos.
`up` usa configuración Docker temporal para imágenes públicas, sin depender del
credential helper interactivo de Windows. ECR usa login local temporal separado;
las credenciales Docker del usuario permanecen intactas. Esto permite ejecutar
el mismo lanzador por SSH mediante `wsl -d Ubuntu`.

Ruta local: API Gateway/JWT → NLB TCP → Istio → micro/PostgreSQL-outbox;
publisher → SQS → Pipe → Step Functions HTTP → mismo NLB → Istio → micro → PDF/S3.
Istio aplica mTLS y autorización entre gateway y micro. HPA usa CPU **y RAM**.
Karpenter AWS provisiona workers Docker reales en EKS local, con pool de práctica
separado. No usa KWOK ni clúster alternativo. Micro permanece en nodos `general`;
llevarlo al pool dinámico requiere resolver distribución de imagen y placement.

## Instalaciones anteriores y reconstrucción

El lanzador no adopta `localstack-aws` ni otro emulador ajeno en 4566. Para migrar
una instalación anterior, usa la [guía de cleanup](CLEANUP.md) y revisa el inventario.
Instalaciones antiguas sin persistencia pueden perder estado incluso al detenerse.
Para conservarlas mientras pruebas un arranque nuevo necesitas otro motor Docker;
otra distribución WSL que comparte Docker Desktop no es un motor aislado.

La laptop de pruebas se reconstruye con el emulador administrado
`micro-test-local-emulator`. Para este usa el ciclo `down --delete-data` / `up`
anterior. Reiniciar el emulador o Docker Desktop por separado puede dejar IPs
guardadas obsoletas; la recuperación con datos conservados no está certificada.

## Primera corrida en entorno limpio

Requisitos previos:

- Docker Desktop con integración WSL2 habilitada y contenedores Linux; acceso al
  socket `/var/run/docker.sock`. No instalar otro daemon sobre esa integración.
- Python 3.11+ con módulo `venv` y `curl`. En Ubuntu, instalar paquete `python3-venv`
  correspondiente a versión de Python. Si apt falla por repositorio externo, resolver
  firma/repositorio antes de repetir; no deshabilitar verificación de firmas.
- Licencia/token LocalStack con EKS, RDS y demás servicios usados habilitados.
- Internet para imágenes y herramientas. Motor limpio y puerto 4566 libre.
  Reserva recursos para control plane, tres workers iniciales y worker Karpenter;
  falta medir mínimo de RAM/CPU de laptop para este arranque.
- Clonar repo preferentemente en filesystem Linux WSL (`~/source/micro-test`),
  por rendimiento de build. Ejecutar desde raíz del checkout.

Introduce token sin guardarlo en historial ni Git. Ejecuta **solo esta línea**,
espera la pregunta, pega token y presiona Enter (entrada oculta):

```bash
read -rsp 'LocalStack Auth Token: ' LOCALSTACK_AUTH_TOKEN
```

Después ejecuta el siguiente bloque. No pegues ambos bloques juntos: `read` puede
consumir una línea de comandos pegada en lugar del token.

```bash
printf '\n'
export LOCALSTACK_AUTH_TOKEN
./localstack/lab up
unset LOCALSTACK_AUTH_TOKEN
```

El lanzador crea venv local, instala dependencias fijadas y binarios verificados
(k3d 5.8.3, kubectl 1.34.1, Istio 1.31.3). No cambia ejecutables del sistema.
Compose fija LocalStack Pro 2026.10.4 por digest, `PERSISTENCE=1`, volumen nombrado
y socket Docker. Publica **solo `127.0.0.1:4566`**, sin NLB/DB abiertos al host.
Cliente `.http` nuevo debe ejecutarse dentro de WSL. Acceso remoto del lab existente
por Tailscale sigue funcionando; nuevo Compose no lo publica automáticamente.

### Qué hace `up`, en orden

1. Rechaza emulador/puerto/volumen ajenos; inicia únicamente contenedor propio.
2. Crea VPC, subredes, EKS 1.34 con tres node groups, ECR, RDS PostgreSQL y S3.
3. Correlaciona k3d/nodos con EKS/EC2; descubre red e IPs. Instala Istio, construye
   micro, comprueba SQL, scheduling, mTLS, autorización y NetworkPolicy.
4. Prueba ECR push/pull. Descubre listener TCP NLB; registra nodo Ready/NodePort
   como target y exige HTTP Task de salud exitoso. No supone puerto 4566 para NLB.
5. Despliega aceptación, authorizer, outbox publisher, SQS/DLQ, Pipe y workflow
   **HTTP desde su primera creación**, sin Lambda de transporte ni cutover posterior.
6. Instala HPA CPU 60% + memoria media inicial 512Mi, con muestras de ambas métricas.
7. Instala Karpenter 1.8.2 con egreso restringido; exige NodeClaim Ready, nodo real
   asociado a EC2 local y contenedor HTTP funcional en ese worker.
8. Activa Pipe/Scheduler y prueba compra/PDF, JWT, idempotencia, reintentos,
   error permanente/recuperación y duplicado tras caída del publisher.
9. Genera `localstack/.state/business/purchase.http` y reporte final de etapas.

Fallo detiene secuencia y deja reporte `localstack/reports/laptop/.../bootstrap.json`;
triggers de negocio quedan detenidos cuando stack existe. Repetir mismo `up` sobre
emulador propio revalida/reconcilia sin borrar datos; no elimina compras de prueba.
Cambios de plantilla que puedan reemplazar datos se rechazan. No reanudar compras
manualmente mientras bootstrap está incompleto.

**`PASS` de bootstrap no certifica escalado bajo carga ni recuperación tras reinicio.**
Configurar volumen no prueba persistencia integral de EKS/RDS/contenedores auxiliares.
Ver estado de evidencia en [VALIDATION.md](VALIDATION.md).

## Uso diario y request .http

Desde raíz, WSL:

```bash
./localstack/lab status
./localstack/lab http       # regenera archivo: guardar antes tus ediciones propias
./localstack/lab test       # crea compras e inyecta fallos; no ejecutar con pruebas concurrentes
./localstack/lab pause      # detiene publisher/Pipe; no cancela workflows en curso
./localstack/lab resume     # salud primero; reanudar cola puede provocar ráfaga
```

En Mac, para **lab existente**:

```bash
./localstack/lab status --endpoint http://100.100.154.68:4566
./localstack/lab http --endpoint http://100.100.154.68:4566
```

Abre `.state/business/purchase.http` con cliente HTTP del editor: `Accept` devuelve
saleId; cópialo y ejecuta `Poll` hasta `COMPLETED`; abre PDF. JWT dura una hora.
Publisher corre cada minuto; ACCEPTED puede esperar en outbox antes de tener
ExecutionArn. Bloques Transient/Permanent/Retry permiten practicar recuperación.

- [Detener, borrar y reconstruir sin prune global](CLEANUP.md).
- [Ejercicios de negocio y logs](business/PRACTICE.md).
- [HPA, Karpenter, cola y carga](scaling/README.md).
- [Aspire: seguimiento gráfico por saleId](observability/README.md).
- [Frontend Angular de ventas](../frontend/README.md).

### Publicar el frontend local

Con el API y el authorizer desplegados por `lab up`, instala Node.js 20.19+ o
22.12+ y npm en WSL. Desde la raíz:

```bash
npm --prefix frontend ci
./localstack/lab frontend
./localstack/lab frontend-status
```

La app queda en S3 local; abre el `frontendUrl` que imprime el comando. El mismo
bucket/objeto se conserva en despliegues repetidos y los assets nuevos se cargan
sin borrar archivos existentes. Para los detalles del origen local, JWT y diseño
futuro AWS/CloudWatch, consulta [frontend](../frontend/README.md) y
[arquitectura AWS](../aws/ARCHITECTURE.md).

`up` no instala Aspire CLI ni arranca visor: observabilidad es cliente opcional,
no dependencia del flujo. Visor actual en Mac: <http://127.0.0.1:18888/traces>.

## Desarrollo y verificación

### Error `env: bash\r: No such file or directory` en WSL

El archivo `localstack/lab` tiene finales de línea Windows (CRLF). Desde raíz del
checkout, dentro de WSL, convierte únicamente ese archivo y comprueba el lanzador:

```bash
sed -i 's/\r$//' localstack/lab
./localstack/lab --help
```

Después repite el comando original. `.gitattributes` fija LF para este lanzador y
scripts `.sh`, incluso con `core.autocrlf=true`. Actualizar las reglas no convierte
necesariamente archivos ya presentes: aplica la corrección anterior a ese checkout.
No hace falta cambiar configuración Git global ni recrear el laboratorio.

### Timeout de licencia en primera etapa `emulator`

`LocalStack Pro/license did not become ready` significa que no se confirmó
LocalStack Pro activo antes del timeout; por sí solo no identifica la causa.
Si el token apareció como `command not found`, vuelve a introducirlo usando
los dos bloques separados del inicio. Si quedó visible en salida compartida,
rótalo en LocalStack y utiliza el nuevo; no publiques logs con secretos.

`up` reutiliza el contenedor existente y no actualiza sus variables. Para un
**primer arranque fallido antes de `foundation`**, tras exportar token correcto,
puedes recrear solo ese emulador nuevo y repetir arranque:

```bash
docker --host unix:///var/run/docker.sock compose -f localstack/compose.yaml \
  up -d --force-recreate --no-deps localstack && \
./localstack/lab up
unset LOCALSTACK_AUTH_TOKEN
```

Recrea `micro-test-local-emulator` conservando volumen nombrado; no borra volúmenes.
No uses esta recuperación para adoptar `localstack-aws` ni reiniciar un laboratorio
ya desplegado. Si vuelve a fallar, revisar estado/logs del contenedor localmente
antes de repetir; puede existir otro problema de licencia, red o inicio.

### Verificación offline

```bash
./localstack/lab verify
```

Pruebas offline, plantillas CloudFormation, contratos y coherencia de documentación.
No certifica despliegue WSL. Tests PostgreSQL están condicionados a base aislada;
se muestran como omitidos si no está habilitada. No usar DB de compras para esos tests.

Estructura: `cloudformation/` base local; `business/` API/outbox/ASL/Lambda;
`kubernetes/` Istio/micro/políticas; `scaling/` NLB/HPA/Karpenter;
`scripts/` funciones internas; `tests/` arranque; `evidence/` evidencia curada.
Scripts internos no constituyen caminos de instalación alternativos.
`.venv/`, `.state/`, reportes y tokens se generan localmente y se ignoran en Git.

## Diferencias frente a AWS real

HTTP Tasks locales usan red Docker y HTTP. No prueban acceso privado gestionado,
VPC Link, TLS de entrada, IAM efectivo, latencia ni costos AWS. NLB conserva tipo
network/internal; target local es NodePort, sin AWS Load Balancer Controller.
Kubeconfig local normaliza `host.docker.internal` a loopback solo tras comprobar
red y puerto publicados del clúster propio; conserva verificación TLS con CA de k3d.
IPs de nodo pueden cambiar: `up` redescubre y valida target, no hay reconciliación
continua. Lambda publisher y authorizer usan credenciales ficticias del emulador.

Persistencia: [documentación LocalStack](https://docs.localstack.cloud/aws/developer-tools/snapshots/persistence/).
Workers reales/Karpenter: [documentación EKS LocalStack](https://docs.localstack.cloud/aws/services/eks/#self-managed-nodes-and-karpenter).
Fuentes describen capacidades; aceptación depende del reporte ejecutado en tu entorno.
