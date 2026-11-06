# CloudFormation: validar sin recursos y desplegar por etapas

Estado revisado: **2026-11-05**. Ejecutar los bloques desde la raíz del repositorio
en **Bash** (macOS, Linux o WSL). Detenerse ante cualquier error.

## 1. Qué podemos hacer hoy

Sí: podemos validar YAML y esquemas CloudFormation sin crear infraestructura.
No existe un dry-run que ejecute toda la arquitectura y garantice su funcionamiento
sin aprovisionarla.

**Este checkout todavía no tiene plantillas AWS reales bajo `aws/`.** Los nueve
CloudFormation existentes son del laboratorio LocalStack. No deben desplegarse
en AWS: incluyen nombres locales, credenciales de prueba, endpoints HTTP y
dependencias creadas por scripts. No basta con cambiar perfil y región.

| Método | Qué comprueba | Efecto en AWS |
|---|---|---|
| `cfn-lint` | Sintaxis, estructura, propiedades, referencias y reglas estáticas | Ninguno; funciona offline después de instalarlo |
| `aws cloudformation validate-template` | Validación básica del documento por CloudFormation; devuelve parámetros y capacidades | Envía la plantilla a AWS; no crea stack ni recursos |
| `create-change-set` o `deploy --no-execute-changeset` | Plan de creación/cambio y validaciones previas soportadas | Crea metadatos de change set; un stack nuevo queda en `REVIEW_IN_PROGRESS`, sin aprovisionar sus recursos |
| `execute-change-set` | Despliegue real | Crea/modifica/elimina recursos; puede generar cargos |

`validate-template` no sustituye el lint de propiedades. Las validaciones previas
actuales de change sets pueden detectar conflictos y determinadas cuotas, pero
no certifican todos los permisos, capacidad, TLS, red ni funcionamiento del negocio.
Fuentes: [validación de plantillas](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/template-guide.html),
[validaciones previas](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/validate-stack-deployments.html),
[creación de change sets](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/create-change-set.html).

Si el requisito es **no modificar nada en la cuenta**, usar únicamente lint local
y, cuando tú lo ejecutes, `validate-template`. Un change set ya modifica metadatos.
Revisar también cualquier macro/transform personalizado antes de planificar: su
procesamiento puede invocar código. Subir artefactos a S3 tampoco es una operación
puramente local. [Procesamiento de macros](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/template-macros-overview.html).

## 2. Validación offline reproducible de los archivos actuales

Requisitos: Python 3.11+ y `venv`. Si ya existe `localstack/.venv` del laboratorio,
utilizarlo. Si no existe, preparar solo las dependencias de esta validación:

```bash
python3 -m venv localstack/.venv
localstack/.venv/bin/python -m pip install 'cfn-lint==1.59.0' 'PyYAML==6.0.3'
```

La instalación descarga paquetes; los siguientes comandos no requieren AWS,
credenciales, Docker ni un emulador encendido:

```bash
localstack/.venv/bin/cfn-lint --version

PYTHONPATH=localstack/scripts:localstack/business \
  localstack/.venv/bin/python -c \
  'from lab import validate; from business import validate as validate_business; validate(); validate_business()'

localstack/.venv/bin/cfn-lint \
  --non-zero-exit-code error \
  --template localstack/scaling/network.yaml
```

Se reutilizan los validadores del repositorio. El primero comprueba las cinco
plantillas base y el orden de sus exports/imports. El de negocio renderiza y valida
tres plantillas. El último valida NLB/probe. No ejecutar `lab up` para hacer lint.

Los validadores regeneran exclusivamente sus archivos de salida en
`localstack/.state/rendered/` y los JSON `artifacts`, `business` y
`publisher-scheduler` de `localstack/.state/business/`.
No usan `inventario.json` ni ejecutan `purchase.http`.

**Negocio requiere renderizado:** `business/templates/business.yaml` contiene
`__BUSINESS_ASL__`. El helper `template()` incorpora `workflow.asl.json` mediante
`Fn::Sub`. El documento comprobado es
`localstack/.state/business/business.json`; no enviar el YAML fuente incompleto
a CloudFormation. Esta validación no ejecuta el workflow.

### Resultado de esta revisión

Con `cfn-lint 1.59.0`: **9 plantillas sin errores**, orden de exports/imports de
las cinco bases aprobado. Se observaron diez warnings en las bases:

- Ocho `W3010`: AZ `us-east-1a` / `us-east-1b` fijas.
- Un `W2501`: contraseña de laboratorio escrita en la plantilla RDS.
- Un `W3691`: el linter advierte que el valor genérico `16` de RDS no es creable.
  Esto no significa que todas las versiones PostgreSQL 16 estén retiradas.

El validador base falla por errores y muestra warnings; no reportar este resultado
como «sin advertencias» ni como aprobación para AWS. No se cambiaron las plantillas.
`validate-template` contra AWS, change sets y despliegue real: **PENDIENTES**.

### Orden de dependencias de las plantillas locales

Inventario para entender el repositorio; **no es una lista para desplegar en AWS**.
El bootstrap incluye pasos entre estas plantillas que no son CloudFormation.

| Orden compatible | Archivo | Dependencia/prerrequisito |
|---|---|---|
| 1 | `localstack/cloudformation/01-network.yaml` | Ninguna; exporta VPC, subredes y SG |
| 2 | `localstack/cloudformation/02-eks.yaml` | Exports de red |
| 3 | `localstack/cloudformation/03-registry.yaml` | Independiente; antes de publicar imagen |
| 4 | `localstack/cloudformation/04-database.yaml` | Subredes y SG de red |
| 5 | `localstack/cloudformation/05-storage.yaml` | Independiente; antes de negocio |
| 6 | `localstack/scaling/network.yaml` | Red; para probar tráfico, EKS/Istio y registro de targets; puerto descubierto por scripts |
| 7 | `localstack/business/templates/artifacts.yaml` | Antes de subir ZIP Lambda |
| 8 | `localstack/business/templates/business.yaml` (renderizado) | DB, ZIP publicado, NLB, Connection y servicios listos |
| 9 | `localstack/business/templates/publisher-scheduler.yaml` | Lambda publisher de negocio; schedule inicialmente deshabilitado |

El Pipe de negocio y otras operaciones del laboratorio se gestionan desde scripts;
aplicar solo estos nueve archivos no reproduce el sistema completo.

## 3. Orden previsto para la futura implementación AWS

**Propuesta de secuencia, no manifiesto ejecutable.** Faltan los archivos AWS,
nombres de stacks, parámetros y contratos de Outputs. Al generarlos, habrá que
registrar esos nombres exactos en esta tabla y verificar sus dependencias.
No reutilizar plantillas retiradas del historial.

| Paso | Entregable | Depende de / condición para avanzar |
|---|---|---|
| 1 | Red: VPC, exactamente dos AZ, subredes, rutas, SG y egreso/endpoints | CIDR y estrategia de egreso definidos |
| 2 | Registro ECR y bucket de artefactos | Cuenta/región y nombres definidos; después publicar imágenes/ZIP versionados |
| 3 | EKS, acceso administrativo, IAM y capacidad base On-Demand | Red; acceso al API privado desde equipo/runner autorizado |
| 4 | RDS privado, secretos y S3 de documentos | Red; perfil RDS, retención y backups definidos |
| 5 | NLB interno, listener/target group, Resource Gateway/Configuration y Connection privada | Red, dominio/TLS e identidad del workflow definidos |
| 6 | SQS/DLQ, Step Functions, Pipe, publisher y Scheduler | DB, acceso privado y artefactos existentes; Pipe STOPPED y Scheduler DISABLED |
| 7 | Bootstrap Kubernetes y migraciones | EKS, imágenes y Outputs previos; instalar controladores/Istio, TLS, TargetGroupBinding, servicios, políticas, HPA y Karpenter |
| 8 | API Gateway autenticado y VPC Link | NLB y servicios saludables; identidad de usuario configurada |
| 9 | Pruebas y activación controlada | Probar rutas, TLS, autorización y datos; habilitar triggers y probar compra → 202 → COMPLETED → PDF |

El paso 7 es **externo a CloudFormation**: Helm, kubectl, certificados y migraciones
necesitan instrucciones y artefactos propios. Publicar imágenes/ZIP también es
una operación explícita. La asignación definitiva de roles/políticas entre stacks
debe evitar ciclos; ningún stack inicial debe importar Outputs de uno posterior.

Antes de generar los YAML faltan cuenta/región, presupuesto, DNS/TLS, identidades,
volumen/duración de pasos y RTO/RPO. **Dos AZ ya están decididas.**

### Por qué no se pueden planificar todos los stacks vacíos en cadena

Un change set de red sin ejecutar no produce VPC, subnets ni exports utilizables.
Un stack EKS que importe esos valores necesita que la red exista. Tampoco sirven
IDs inventados para parámetros de recursos existentes.

Para revisar todo sin recursos: lint de todas las plantillas, revisión de contratos
entre stacks y validación básica individual. Para un plan AWS completo por stack:
las dependencias deben existir; revisar/ejecutar/esperar un stack antes de pasar al
siguiente. Los exports se publican al crear o actualizar el stack, no al preparar
su change set. [Outputs entre stacks](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-stack-exports.html).

## 4. Procedimiento AWS por stack, cuando existan los YAML reales

**Los comandos de esta sección quedan preparados para que tú los ejecutes.**
No apuntar `CFN_TEMPLATE` a `localstack/`. Repetir los pasos 4.2–4.5 para cada stack
en el orden definitivo. No lanzar todos los despliegues en paralelo.

### 4.1. Seleccionar cuenta y región

Usar AWS CLI v2 y un perfil AWS real autenticado. Abrir una terminal separada del
laboratorio, sin credenciales `test/test`. Sustituir los valores entre `<...>`:

```bash
export CFN_PROFILE='<perfil-AWS-real>'
export CFN_REGION='<region-destino>'
export AWS_IGNORE_CONFIGURED_ENDPOINT_URLS=true
export AWS_PAGER=''

aws --profile "$CFN_PROFILE" --region "$CFN_REGION" \
  sts get-caller-identity

cfn() {
  aws --profile "$CFN_PROFILE" --region "$CFN_REGION" \
    --no-cli-pager cloudformation "$@"
}
```

Comprobar `Account` y `Arn` antes de continuar. No añadir `--endpoint-url`.
La variable de endpoints evita heredar URLs de LocalStack del entorno/configuración.
[Configuración oficial de endpoints](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-endpoints.html).

Permisos según etapa: `cloudformation:ValidateTemplate` para validar; permisos de
crear/describir/eliminar change sets para planificar; permisos de ejecución y del
rol CloudFormation para desplegar. Reconocer `CAPABILITY_IAM` no concede permisos.

### 4.2. Seleccionar y validar la plantilla

```bash
CFN_TEMPLATE='aws/<ruta-real-de-plantilla.yaml>'
CFN_STACK='<nombre-del-stack>'
CFN_PARAMS='aws/<ruta-real-de-parametros.json>'

test -f "$CFN_TEMPLATE" && test -f "$CFN_PARAMS"

localstack/.venv/bin/cfn-lint \
  --regions "$CFN_REGION" --template "$CFN_TEMPLATE"

cfn validate-template --template-body "file://$CFN_TEMPLATE"
```

Estos son marcadores de posición, no archivos entregados. No avanzar si los
archivos faltan o alguno de los comandos falla. El lint AWS usa el umbral por
defecto: revisar y resolver warnings, sin heredar excepciones del laboratorio.

`validate-template` no acepta valores de parámetros ni despliega dependencias.
Para `create-change-set`, el JSON de parámetros tiene formato de lista:

```json
[
  {"ParameterKey": "<nombre-exacto-en-Parameters>", "ParameterValue": "<valor-real>"}
]
```

Usar `[]` si no hay parámetros. En actualizaciones, usar `UsePreviousValue: true`
para conservar un parámetro existente, sin `ParameterValue` en esa entrada.
No incluir contraseñas/tokens en Git ni argumentos: diseñar referencias gestionadas
a Secrets Manager/SSM. Las claves exactas se documentarán con las plantillas.

`--template-body` admite hasta **51.200 bytes**. Para archivos mayores se necesita
`--template-url` de un artefacto accesible en S3 (hasta 1 MB); publicar ese artefacto
es otro paso explícito. [Referencia de validate-template](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/validate-template.html).

### 4.3. Crear el plan sin ejecutarlo

Elegir `CREATE` para un stack nuevo; `UPDATE` para uno ya desplegado. Un stack nuevo
pendiente en `REVIEW_IN_PROGRESS` sigue requiriendo tipo `CREATE`.

```bash
CFN_CHANGE_TYPE=CREATE
CFN_CHANGE_SET="review-$(date -u +%Y%m%dT%H%M%SZ)"

CFN_CAPABILITIES=()
# Si la plantilla contiene IAM, elegir UNA de estas asignaciones:
# CFN_CAPABILITIES=(--capabilities CAPABILITY_IAM)
# CFN_CAPABILITIES=(--capabilities CAPABILITY_NAMED_IAM) # IAM con nombres explícitos

cfn create-change-set \
  --stack-name "$CFN_STACK" \
  --change-set-name "$CFN_CHANGE_SET" \
  --change-set-type "$CFN_CHANGE_TYPE" \
  --template-body "file://$CFN_TEMPLATE" \
  --parameters "file://$CFN_PARAMS" \
  "${CFN_CAPABILITIES[@]}"

cfn wait change-set-create-complete \
  --stack-name "$CFN_STACK" --change-set-name "$CFN_CHANGE_SET"

cfn describe-change-set \
  --stack-name "$CFN_STACK" --change-set-name "$CFN_CHANGE_SET"
```

Si se usa un rol de despliegue dedicado, añadir `--role-arn` con su ARN real al
crear el change set; requiere `iam:PassRole` según configuración. No inventar ARN.

Revisar `Status`, `ExecutionStatus`, `StatusReason` y `Changes`, especialmente
`Action`, `Replacement` y recursos persistentes. Consultar también los resultados
de validación en la consola: puede haber advertencias aunque el plan se complete.
Si falla el waiter, ejecutar igualmente `describe-change-set` para obtener la causa;
no continuar a ejecución. Un UPDATE sin diferencias puede devolver `FAILED` con
motivo «sin cambios»: no equivale a plantilla inválida ni requiere desplegar.

**Aquí termina el dry-run.** `deploy --no-execute-changeset` es otra forma de preparar
un plan, pero `deploy` sin esa opción también lo ejecuta. Esta guía usa operaciones
separadas para que el paso de despliegue sea explícito.
[Referencia de deploy](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/deploy.html).

Para descartar únicamente el plan:

```bash
cfn delete-change-set \
  --stack-name "$CFN_STACK" --change-set-name "$CFN_CHANGE_SET"
```

En una creación puede quedar el contenedor vacío `REVIEW_IN_PROGRESS`. Eliminarlo
solo después de comprobar su estado y ausencia de recursos; no usar `delete-stack`
como limpieza genérica sobre stacks existentes.

### 4.4. Ejecutar realmente el change set revisado

**Este paso crea o modifica recursos reales y puede generar cargos. Omitirlo si
solo se desea validar.** Ejecutar solo un plan revisado con estado ejecutable.

```bash
cfn execute-change-set \
  --stack-name "$CFN_STACK" --change-set-name "$CFN_CHANGE_SET"

if [ "$CFN_CHANGE_TYPE" = CREATE ]; then
  cfn wait stack-create-complete --stack-name "$CFN_STACK"
else
  cfn wait stack-update-complete --stack-name "$CFN_STACK"
fi
```

El waiter puede agotar su tiempo mientras el stack sigue trabajando. Consultar
estado/eventos antes de repetir una operación. No avanzar al siguiente stack
hasta `CREATE_COMPLETE` o `UPDATE_COMPLETE` y comprobación de sus prerrequisitos.

### 4.5. Recoger Outputs y continuar

```bash
cfn describe-stacks --stack-name "$CFN_STACK" \
  --query 'Stacks[0].{Status:StackStatus,Outputs:Outputs}'

cfn list-exports
```

Si la siguiente plantilla usa `Fn::ImportValue`, comprobar que existan los nombres
esperados. Si recibe IDs/ARN como parámetros, copiar los Outputs reales al archivo
de parámetros siguiente. No pasar secretos como Outputs.

`CREATE_COMPLETE` no certifica conectividad ni compra/PDF. Ejecutar los pasos
Kubernetes, migraciones y pruebas de aceptación correspondientes antes de activar
el tráfico y los consumidores.

## 5. Fallos, actualización y eliminación

Ante un fallo, detener la secuencia y revisar:

```bash
cfn describe-stacks --stack-name "$CFN_STACK" \
  --query 'Stacks[0].{Status:StackStatus,Reason:StackStatusReason}'
cfn describe-stack-events --stack-name "$CFN_STACK"
```

Corregir la causa antes de preparar otro plan. Un stack nuevo en `ROLLBACK_COMPLETE`
no se actualiza como un stack sano; revisar recursos retenidos antes de eliminarlo
y recrearlo. En `UPDATE_ROLLBACK_FAILED`, resolver el recurso que impide rollback
antes de continuarlo. No desactivar rollback ni omitir recursos automáticamente.

Para actualizar: repetir lint → `validate-template` → change set `UPDATE` → revisión
→ ejecución → espera → pruebas. Conservar versión previa de plantillas y artefactos;
una migración de datos puede necesitar recuperación específica.

Para eliminar: pausar entrada y consumidores, respaldar datos, revisar políticas
`DeletionPolicy`/`UpdateReplacePolicy` y quitar consumidores antes de dependencias.
CloudFormation impide eliminar exports todavía importados. RDS, snapshots, buckets
con objetos/versiones y recursos retenidos requieren tratamiento explícito.
No hay cleanup AWS definitivo hasta que existan las plantillas y su política de datos.

La siguiente entrega necesaria para un paso a paso AWS completamente ejecutable
es generar los YAML reales, sus parámetros y artefactos, y reemplazar los marcadores
de esta guía por nombres y Outputs comprobados.
