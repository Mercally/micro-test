# Evidencia y pendientes — 2026-11-03

> Reporte histórico del host anterior. Para laptop y extensión de compras, consultar
> [business/VALIDATION.md](business/VALIDATION.md), fechado 2026-11-04. Los resultados
> siguientes no aprueban el runtime actual.
>
> Continuación laptop: [compras/Istio y pruebas pendientes](business/VALIDATION.md).
> [Karpenter con workers Docker reales](karpenter/README.md) preparado en clúster
> adicional; validación estática aprobada, instalación/escalado pendientes en WSL.

Ejecutado desde Mac contra gateway Tailscale autorizado
`http://100.95.224.37:4566`, cuenta emulada `000000000000`, región `us-east-1`.
Imagen reportada: `2026.10.4:a39e30ef`, edición Pro, licencia activa.
No se verificó el nombre comercial del plan mediante API ni el digest de imagen
Docker de PC. Solo `localstack/` aparece como cambio nuevo en Git.

| Comprobación | Resultado y alcance |
|---|---|
| 11 plantillas renderizadas, referencias, imports y orden | PASS; cfn-lint 1.59.0 sin errores. W3010/W3691/W2501 documentados como elecciones locales |
| 27 objetos Kubernetes/Istio | YAML parseado; no validación contra API Kubernetes ni instalación ejecutada |
| Python y guardias | 7 tests offline PASS; compilación sintáctica PASS; endpoints externos/redirects rechazados; credenciales heredadas removidas; cleanup conserva datos/dependencias |
| Despliegue local | 10 stacks completados; segunda ejecución los reconoce unchanged. API opcional del micro no desplegada |
| VPC/routing/SG | 8 subredes y 8 asociaciones leídas por EC2 API. No demuestra tráfico ni firewall |
| EKS | API ACTIVE, versión solicitada 1.34; 3 Managed Node Groups ACTIVE, labels y taints presentes. Workers/pods no verificados |
| RDS/secreto | API available, grupos y secreto leídos. SQL, versión real del motor y TLS no verificados |
| ECR | Repositorio/URI leídos. Push/pull y autenticación de nodos pendientes |
| S3 | Bucket existente, versionado Enabled. PDF del micro pendiente |
| Lambda/API Gateway | Invocación directa y POST HTTP real devuelven payload esperado |
| Logs | Eventos diagnósticos leídos; grupo convencional tiene stream real. LoggingConfig personalizado no se aplica correctamente en esta instalación |
| SQS/Pipe/Step Functions/Lambda | PASS con diagnosticId único, value=73 y salida íntegra. Ejecución del Pipe distinta de la invocación directa del target |
| Pipe al finalizar | CurrentState=STOPPED y DesiredState=STOPPED, consultados tras smoke |
| DLQ/redrive | PASS: mensaje vuelve a ser recibido y termina en DLQ con el mismo payload |
| IAM | FAIL esperado como hallazgo: usuario temporal sin políticas puede leer bucket. No se declara aprobado el aislamiento IAM |
| Conectividad desde Mac | Gateway 4566 responde. TCP 4510 (DB) y 4511 (Kubernetes) timeout; no se reescribieron kubeconfig ni URLs de servicio para simular acceso |
| Istio/mTLS/NetworkPolicy/SQL/HTTP/PDF | PENDIENTE: requieren bootstrap WSL, Docker y Kubernetes locales |
| Persistencia/reinicio | NO EJECUTADO: health indica persistence=disabled; reiniciar podría perder datos del emulador compartido |
| Limpieza completa | NO EJECUTADA para conservar laboratorio. Dos Pipes diagnósticos propios se eliminaron durante investigación; workaround documentado |
| AWS real / archivos existentes | Ninguna llamada de administración a AWS real; archivos existentes conservados |

Los JSON son evidencia puntual, no certificados de que recursos sigan disponibles
después de cambiar o reiniciar PC. Los fixtures no prueban ventas asíncronas ni
funciones de negocio ausentes.

## Evidencia conservada

- [coverage.json](reports/coverage.json): identificación y respuestas iniciales de APIs/registro.
- [deployment.json](reports/deployment.json): stacks, recursos y propiedades leídas por servicios.
- [eks-api.json](reports/eks-api.json): EKS/nodegroups; declara runtime no verificado.
- [diagnostic.json](reports/diagnostic.json): payload, ejecución SQS/Pipe y salida Lambda; estado final detenido.
- [redrive.json](reports/redrive.json): comprobación de DLQ con payload correlacionado.
- [iam.json](reports/iam.json): fallo de denegación sin políticas.
- [pipe-lifecycle.json](reports/pipe-lifecycle.json): discrepancias de ciclo de vida comprobadas.
- [lambda-logs.json](reports/lambda-logs.json): eventos diagnósticos archivados antes de normalizar grupo de logs.
- [remote-connectivity.json](reports/remote-connectivity.json): puertos de DB/Kubernetes no alcanzables desde Mac.

## Comandos pendientes

La secuencia completa y parámetros están en [README.md](README.md). Desde WSL,
después de seleccionar nombres Docker/k3d observados:

```bash
localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint http://127.0.0.1:4566 --k3d-cluster "$LAB_K3D_CLUSTER" bootstrap \
  --localstack-container "$LAB_LOCALSTACK_CONTAINER" --docker-network "$LAB_DOCKER_NETWORK" \
  --database rds --presign-endpoint http://100.95.224.37:4566
localstack/.venv/bin/python localstack/scripts/lab.py --endpoint http://127.0.0.1:4566 \
  deploy --only micro-api --parameters localstack/.state/micro-api-parameters.json
localstack/.venv/bin/python localstack/scripts/kube.py \
  --endpoint http://127.0.0.1:4566 --k3d-cluster "$LAB_K3D_CLUSTER" registry
```

Después, ejecutar `checks.py micro` con URL API generada. Preparar una sesión con
enforcement IAM activo y repetir `checks.py iam`. Configurar persistencia durable
antes de un reinicio intencional y ejecutar `checks.py restore`. No reiniciar el
emulador actual para fingir una prueba de persistencia aprobada.
