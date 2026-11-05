# Lambda y NodePort: reparación de red local


Para la instalación observada, Lambda usa `bridge`; k3d usa su propia red.
Aplica `micro-test-lambda-network-fix.zip` y ejecuta en WSL:

```bash
cd /mnt/c/Users/josue/source/repos/micro-test-local-lab
export PATH="$PWD/localstack/.state/tools:$HOME/.local/bin:$PATH"
localstack/.venv/bin/python localstack/business/wsl.py \
  --repair-lambda-network \
  --network k3d-micro-test-local-50dacdf7
```

Este modo verifica EKS/k3d y exige Pipe STOPPED/Scheduler DISABLED. Conecta un único
nodo Ready propio a `bridge` si falta, con prioridad de gateway -1. No modifica
rutas globales ni reinicia contenedores. Usa IP de LocalStack en esa red para AWS
API/RDS y la IP del nodo en esa misma red para NodePort. Preserva base/puerto y
respalda parámetros anteriores en el reporte. Actualiza stack con recibo de
despliegue existente; rechaza cambios ajenos. No construye imagen ni repite ECR.

`Runtime health PASS` exige publisher → DB/outbox y transport → health del micro.
Pipe y Scheduler permanecen detenidos. Después:

```bash
localstack/.venv/bin/python localstack/business/business.py start --endpoint http://127.0.0.1:4566 &&
localstack/.venv/bin/python localstack/business/business.py test --endpoint http://127.0.0.1:4566 &&
localstack/.venv/bin/python localstack/business/business.py http --endpoint http://127.0.0.1:4566
```

`http` genera `.state/business/purchase.http` con token local; abrirlo en REST Client
y ejecutar solicitudes en orden. Para ejecutarlo desde Mac, generar allí con endpoint
`http://100.100.154.68:4566`. No guardar el archivo generado en Git.

La conexión del nodo sirve también a nuevos runtimes Lambda que nazcan en `bridge`;
no depende de conectar manualmente contenedores Lambda efímeros. Si k3d recrea ese
nodo o cambian IPs/redes, detener disparadores y repetir reparación. Bootstrap
normal también prepara estos endpoints antes de desplegar negocio.
Esta adaptación Docker no demuestra paridad VPC/SG ni persistencia de datos.

Docker soporta [conexión de contenedores y prioridad de gateway](https://docs.docker.com/reference/cli/docker/network/connect/).
Las [redes bridge aíslan tráfico entre redes por defecto](https://docs.docker.com/engine/network/drivers/bridge/).
