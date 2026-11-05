# LocalStack: punto único de arranque

**Entrada:** `./localstack/lab`. Destino: Windows/WSL2 x86_64 con Docker Linux.
AWS real queda fuera de estos comandos. Diseño siguiente: [AWS](../aws/README.md).

Ruta local: API Gateway/JWT → NLB TCP → Istio → micro/PostgreSQL-outbox;
publisher → SQS → Pipe → Step Functions HTTP → mismo NLB → Istio → micro → PDF/S3.
Istio aplica mTLS y autorización entre gateway y micro. HPA usa CPU **y RAM**.
Karpenter AWS provisiona workers Docker reales en EKS local, con pool de práctica
separado. No usa KWOK ni clúster alternativo. Micro permanece en nodos `general`;
llevarlo al pool dinámico requiere resolver distribución de imagen y placement.

## Laboratorio existente y reconstrucción

Laboratorio de laptop ya funciona. **No ejecutes `up` para migrarlo ni reinicies su
emulador:** persistencia anterior está deshabilitada. Nuevo `up` se niega a adoptar
`localstack-aws` o cualquier contenedor ajeno en 4566. Tampoco borra stacks/volúmenes.
Usa `status`, `http`, `test`, `pause` y `resume` para operar lab existente.

Para validar arranque desde cero conservando ese lab necesitas **otro motor Docker
aislado**, por ejemplo otra máquina/VM de pruebas. Otra distribución WSL que comparte
Docker Desktop NO es otro motor. No se lanza segundo emulador sobre mismo puerto,
ni se detiene el primero automáticamente.

Si decides descartar el lab actual y reconstruir en el mismo motor, usa la
[guía de cleanup](CLEANUP.md): inventario revisable, parada, borrado explícito de
datos y limpieza de archivos generados. Con persistencia anterior deshabilitada,
incluso detenerlo puede perder estado. No ejecutes borrado sin revisar el plan.
La nueva configuración usa los mismos nombres de recursos dentro de emulador nuevo.

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

Introduce token sin guardarlo en historial ni Git y lanza:

```bash
read -rsp 'LocalStack Auth Token: ' LOCALSTACK_AUTH_TOKEN
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
IPs de nodo pueden cambiar: `up` redescubre y valida target, no hay reconciliación
continua. Lambda publisher y authorizer usan credenciales ficticias del emulador.

Persistencia: [documentación LocalStack](https://docs.localstack.cloud/aws/developer-tools/snapshots/persistence/).
Workers reales/Karpenter: [documentación EKS LocalStack](https://docs.localstack.cloud/aws/services/eks/#self-managed-nodes-and-karpenter).
Fuentes describen capacidades; aceptación depende del reporte ejecutado en tu entorno.
