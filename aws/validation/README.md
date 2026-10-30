# Validación local — 29 de octubre de 2026

Esta evidencia corresponde al laboratorio privado con ocho subredes y tres Managed Node Groups. Se retiraron los informes del diseño anterior para no presentarlos como validación de estos archivos.

| Comprobación | Resultado |
|---|---|
| `python3 aws/scripts/validate.py` | OK: tres plantillas, 45 exports, imports/referencias y dependencias sin ciclos |
| Red y permisos declarados | OK: CIDR/AZ exactos, ocho asociaciones/tablas, sin salida a Internet, endpoints, SG, IAM y NetworkPolicies |
| Aplicaciones | OK: tres Deployments con misma imagen parametrizada, ServiceAccounts/Pod Identity por namespace, configuración JDBC/S3 real y probes Spring |
| Datos y mensajería | OK estático: PostgreSQL privado, TLS, SQS/DLQ y ASL de dispatcher sincronizada |
| Casos negativos | Rechazados: subred pública, API Kubernetes pública, RDS público e imagen externa en renderer |
| `cfn-lint 1.58.0` | Sin errores ni advertencias; solo W3010 excluido porque AZ fijas son requisito |
| `kubeconform 0.7.0 -strict -summary -kubernetes-version 1.35.0 aws/kubernetes/*.yaml` | 22 recursos válidos; cero inválidos, errores u omitidos |
| Python | Tres scripts compilan |
| Preflight con ejemplos incompletos | Rechaza `REQUIRED_*` antes de llamar AWS, resultado esperado |
| Mermaid CLI 12.0.0 | Fuente compilada a SVG; PNG temporal revisado visualmente |
| `git diff --check` | Sin errores de whitespace |

La comprobación de esquema Kubernetes 1.35.0 verifica estructura de manifiestos; no selecciona ni verifica la versión que debe usarse en EKS. Los parámetros de versión permanecen obligatorios.

No se consultó la cuenta AWS, no se ejecutó CloudFormation ValidateTemplate ni se crearon/actualizaron recursos. Se consultaron fuentes públicas de AWS para revisar requisitos y se descargaron herramientas/esquemas públicos para validación local.

No se ejecutó el SQL ni la imagen con RDS; no se comprobaron cuotas, SCP/IAM efectivos, costos, disponibilidad actual de versiones/clases/AMI, resolución DNS, bootstrap real de addons, emisión de Pod Identity ni aceptación de políticas en un clúster. La matriz de pruebas posteriores y secuencia `standard → políticas CoreDNS → strict → aplicaciones` están en [README](../README.md).

Repetir validación local:

```bash
python3 aws/scripts/validate.py
cfn-lint -r us-east-1 -t aws/cloudformation/*.yaml --ignore-checks W3010
kubeconform -strict -summary -kubernetes-version 1.35.0 aws/kubernetes/*.yaml
```

Preflight de catálogos es opcional y de lectura; requiere completar parámetros reales antes de ejecutarlo. No sustituye pruebas de aislamiento en recursos reales.
