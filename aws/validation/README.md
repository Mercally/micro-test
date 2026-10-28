# Evidencia de validación — 28 de octubre de 2026

Actualización tras el intento de despliegue del usuario: EKS rechazó comas en el tag `PrivateConnectivity`, un caso que `ValidateTemplate` y el lint inicial no detectaron. Se corrigió el consumidor en `02-eks.yaml` y se agregó una comprobación local que reproduce el fallo anterior y verifica caracteres, longitud y conservación del import. La corrección no implica un nuevo despliegue por parte del asistente.

No se crearon stacks, change sets, repositorios ni otros recursos AWS. Se hicieron consultas de catálogos, precios y `ValidateTemplate`. Se inicializó CodeGraph con `codegraph init -i` por solicitud expresa.

| Comprobación | Resultado |
|---|---|
| YAML/JSON y claves duplicadas | OK, `scripts/validate.py` |
| Referencias, DependsOn, 30 exports/imports y ciclos entre recursos/stacks | OK |
| Seis CIDR exactos, no solapamiento, AZ, asociaciones y rutas | OK |
| Endpoints, DNS privado, parámetros admin y contrato Service/TGB | OK |
| Casos negativos de IP: `/0`, privada, red no `/32`, IPv6 e IPv4 inválida | Rechazados según validación correspondiente |
| cfn-lint 1.58.0 | Sin errores; W3010 omitido por requisito de AZ fijas |
| CloudFormation `validate-template` en `us-east-1` | Cuatro plantillas aceptadas; 02/03 requieren CAPABILITY_IAM |
| Catálogo EKS: 1.35 | STANDARD_SUPPORT, fin 2027-03-27 |
| Cuatro addons fijados | Compatibles con Kubernetes 1.35, amd64 y EC2 |
| AMI de SSM | AL2023 x86_64 estándar, estado available, release 1.35.9-20261026 |
| Servicios de endpoints | Siete tipos requeridos disponibles en ambas AZ; aceptan políticas |
| Helm 3.19.0: lint y template con Kubernetes 1.35 | OK; chart 3.6.2, CRD incluidas, imagen privada y flags de aislamiento presentes |
| Fuentes de imágenes | Manifests linux/amd64 verificados por digest |
| Backend nginx en Docker local | Configuración válida, `/healthz` 200 y JSON esperado; UID/GID 101, solo lectura, sin capabilities; contenedor retirado |
| Mermaid CLI 12.0.0 | Mermaid compilado a SVG y revisión visual mediante PNG temporal |

Archivos de evidencia: [aws-compatibility.json](aws-compatibility.json), [images.json](images.json), [ec2-price.json](ec2-price.json). Los parámetros administrativos no se validaron contra datos reales: permanecen placeholders obligatorios y `adminParametersValidated` es `false` en la consulta de compatibilidad.

`ValidateTemplate` y cfn-lint no prueban un despliegue. No se comprobaron permisos efectivos/SCP, cuotas de cuenta, capacidad EC2, health de addons desplegados, emisión real de credenciales Pod Identity, acceso público desde tu IP, reconciliación TGB ni llamada firmada a una API real. El README contiene sus pasos de aceptación para cuando se despliegue.

La consulta inicial de imágenes mediante mirror ECR Public devolvió HTTP 429. Se consultó la fuente Docker Hub de nginx y se verificó/pulló su digest amd64; el manifest oficial del controlador en ECR Public también se verificó. No se publicó ninguna imagen a ECR.

Para repetir controles locales:

```bash
python3 aws/scripts/validate.py
cfn-lint -r us-east-1 -t aws/cloudformation/*.yaml --ignore-checks W3010
```

Para repetir compatibilidad AWS sin despliegue:

```bash
python3 aws/scripts/preflight.py --compatibility-only --report /tmp/demo-seguros-compatibility.json
```

Antes del despliegue, ejecutar preflight sin `--compatibility-only` con el archivo local que contiene tu IP `/32` y ARN IAM. No usar los placeholders como valores reales.
