# Laboratorio privado: mismo micro, tres roles

Región **us-east-1**, AZ **us-east-1a / us-east-1b**. Estos archivos son una guía de recursos para creación manual. No se ha desplegado ni modificado AWS durante esta revisión.

Un EKS privado contiene tres Managed Node Groups: `general`, `terceros` y `onprem`. Cada grupo abarca sus dos subredes. Los tres Deployments ejecutan **exactamente el mismo digest de tu micro Spring Boot**, con configuración propia y rol IAM asignado a sus pods mediante ServiceAccount + EKS Pod Identity. Los addons conservan sus imágenes de sistema. No se agrega otro micro ni un worker.

`onprem` y `terceros` son simulaciones dentro de esta VPC. No hay conexión a una red corporativa ni a proveedores externos. Sin IGW, NAT, subredes públicas, VPN, Direct Connect ni Transit Gateway. Servicios HTTP solo `ClusterIP`; administración y pruebas desde un host dentro de la VPC.

## Archivos y dependencias

| Archivo | Recursos / uso |
|---|---|
| [01-network.yaml](cloudformation/01-network.yaml) | VPC, ocho subredes y ocho tablas asociadas, SG y once endpoints |
| [02-eks.yaml](cloudformation/02-eks.yaml) | EKS privado, tres NG, cuatro addons, ECR, IAM del sistema y acceso admin |
| [03-services.yaml](cloudformation/03-services.yaml) | RDS PostgreSQL, SQS/DLQ, dispatcher Step Functions, tres buckets y tres roles/identidades de pods |
| [demo.yaml](kubernetes/demo.yaml) | Tres namespaces, ServiceAccounts, ConfigMaps, Deployments y Services; una imagen común |
| [network-policies.yaml](kubernetes/network-policies.yaml) | Denegación por defecto y flujos de aplicación permitidos |
| [system-network-policy.yaml](kubernetes/system-network-policy.yaml) | CoreDNS compatible con CNI estricto |
| [init.sql](sql/init.sql) | Bases lógicas y usuarios independientes; esquema existente; permisos DML |
| [parameters/](parameters/) | Entradas explícitas; `REQUIRED_*` debe sustituirse por valores reales |
| [diagrams/](diagrams/) | Mermaid, SVG regenerado y CSV de direccionamiento |
| [future-stacks.md](future-stacks.md) | Decisiones pendientes y límites funcionales |

Dependencia: **01 → 02 → 03**; 03 también importa 01. Completar cada componente antes del siguiente. Todos los imports tienen export con prefijo `${AWS::StackName}`; nombres de ejemplo: `demo-seguros-networking`, `demo-seguros-eks`, `demo-seguros-services`. Creando recursos uno a uno en consola, `Ref`, `GetAtt` e `ImportValue` indican qué ID/ARN/salida copiar del recurso anterior.

Se retiraron las plantillas de ingreso/API, TargetGroupBinding, valores del controlador y evidencias del diseño anterior. **No usar estos archivos como actualización directa de stacks anteriores existentes**: los cambios de CIDR, grupos y recursos pueden reemplazarlos. No se preparó migración ni se borró ningún recurso de cuenta.

## Red

| Rol | AZ a | AZ b | Tablas explícitas |
|---|---|---|---|
| general | `10.20.11.0/24` | `10.20.12.0/24` | GeneralRouteTableA/B |
| terceros | `10.20.13.0/24` | `10.20.14.0/24` | TercerosRouteTableA/B |
| onprem | `10.20.15.0/24` | `10.20.16.0/24` | OnpremRouteTableA/B |
| DB | `10.20.21.0/24` | `10.20.22.0/24` | DbRouteTableA/B |

VPC `10.20.0.0/16`; cada `/24` tiene 251 direcciones utilizables antes de ENI, nodos y pods. Ocho asociaciones explícitas, una por subred. Todas las tablas incluyen ruta local; las seis de cómputo agregan únicamente gateway S3. Las tablas DB solo tienen ruta local. No hay rutas por defecto. La tabla principal creada por AWS queda sin subredes del laboratorio asociadas implícitamente.

Services Kubernetes usan `172.20.0.0/16`, que no es una subred AWS. El control plane conecta ENI en general A/B. No se fijan IP de pods, RDS ni endpoints. Un nodo inicial por NG (`Min/Desired/Max = 1/1/2`) reduce costo: **tres nodos en total, sin garantía de un nodo por AZ**. Dos subredes por NG no equivalen a alta disponibilidad. No hay autoscaler; `MaxSize` no escala solo.

## Aislamiento efectivo

| Origen | Destino permitido | Control |
|---|---|---|
| general | micro terceros/onprem, TCP 8080 | SG de general + NetworkPolicy por namespace y pod |
| terceros/onprem | Sin HTTP a otros roles | Sin regla SG ni permiso NetworkPolicy |
| cada micro | RDS TCP 5432 | SG de su NG; login solo a su base lógica |
| cada micro | Su bucket y prefijo `sales/*` | Rol IAM propio, endpoint S3 y política de bucket |
| general | `states:StartExecution` del dispatcher | ARN exacto y endpoint privado `states` |
| terceros | Receive/Delete/ChangeVisibility/GetAttributes de cola de trabajo | ARN exacto y endpoint privado `sqs` |
| onprem | Ningún permiso SQS/Step Functions | Rol solo de almacenamiento de su simulador |
| cada micro | DNS y credenciales Pod Identity | CoreDNS TCP/UDP 53 y `169.254.170.23:80` |
| host admin | API Kubernetes 443, RDS 5432 y endpoints 443 | AdminSecurityGroup, IAM y Access Entry |

Los permisos de mensajería son un reparto de laboratorio explícito, pendiente de ajustar al contrato de negocio. **No hacen que el código actual empiece a consumir SQS ni iniciar ejecuciones.** No se añaden variables ficticias `APP_ROLE` ni perfiles Spring inexistentes. Configuración utilizada: `SPRING_APPLICATION_NAME`, `DATABASE_*`, `S3_BUCKET`, región, simulación de fallos y pool JDBC.

Los nodos usan launch templates con un SG distinto por rol. No heredan el SG compartido del clúster; por eso no existe una regla compartida `self/all` que habilite tráfico entre los tres NG. Control plane → kubelet 10250, HTTPS y DNS tiene reglas explícitas. Se usa una regla de salida inerte a loopback para evitar que EC2 instale automáticamente salida abierta en grupos sin reglas.

CNI administra NetworkPolicies en modo **strict** al finalizar bootstrap. Namespaces de aplicación tienen Pod Security `restricted`; pods no privilegiados, sin hostNetwork/hostPath, sin token Kubernetes automático, sin capabilities y con filesystem de solo lectura. IMDSv2 requiere tokens y hop limit 1; CNI tiene rol propio, separado del rol de nodos. Namespace, taint o selector por sí solos no son una barrera de seguridad. No se concede RBAC a aplicaciones para editar políticas, pods o ServiceAccounts.

La regla NetworkPolicy de HTTPS fuera de rangos privados permite las IP públicas que S3 usa incluso con gateway endpoint. **No abre Internet:** SG limita ese destino a la managed prefix list regional S3, y las tablas solo enrutan S3. HTTPS privado a general A/B permite ENI de endpoints; sus políticas e IAM limitan acciones y recursos. SG/IAM siguen siendo necesarios porque NetworkPolicy no filtra FQDN ni ARN.

El administrador y el sistema del clúster son confiables: un cluster-admin puede cambiar políticas, usar exec/port-forward y modificar workloads. Tampoco se aísla un nodo comprometido con NetworkPolicy. Estas limitaciones no deben confundirse con acceso permitido a los pods normales.

## Endpoints y administración

Diez endpoints **Interface**, cada uno con una ENI en general A y otra en general B, DNS privado y HTTPS desde SG de nodos/admin:

| Endpoint | Propósito |
|---|---|
| `ec2` | VPC CNI y metadatos EC2 necesarios para nodos |
| `ecr.api`, `ecr.dkr` | Pull de imágenes privadas y addons |
| `eks` | DescribeCluster desde host admin; no es API Kubernetes |
| `eks-auth` | Credenciales Pod Identity; sin IRSA ni endpoint STS |
| `sqs`, `states` | APIs privadas para permisos de mensajería/orquestación |
| `ssm`, `ssmmessages` | Sesiones SSM del host administrador privado |
| `secretsmanager` | Obtener secreto maestro RDS desde host autorizado |

Un endpoint **Gateway S3** en las seis tablas de cómputo permite capas ECR y tres buckets del micro. Capas ECR se restringen al bucket regional de AWS; aplicaciones a buckets de esta cuenta. `S3PrefixListId` debe ser la lista real `com.amazonaws.us-east-1.s3`, nunca un ID inventado. La política ECR permite repositorios de addons de AWS, que pertenecen a otras cuentas; IAM del solicitante sigue siendo obligatorio.

No hace falta endpoint `rds` para SQL: conexión va al DNS privado de la instancia por ruta local. No se instala exportador de logs/OTel; `OTEL_SDK_DISABLED=true` deshabilita intentos del agente incluido en tu imagen. Logs del control plane los entrega EKS; `kubectl logs` funciona a través de API/kubelet. Si se incorpora exportación desde pods, añadir su destino, endpoint y permisos antes.

**Host admin pendiente de crear manualmente** en general A/B, sin IP pública, con AdminSecurityGroup, SSM Agent reciente y herramientas ya instaladas en la AMI. Necesita perfil de instancia para SSM; la identidad usada para kubectl debe coincidir con `AdminPrincipalArn` y tener `eks:DescribeCluster`. Si se asume otro rol desde ese host, añadir STS regional y sus permisos, o usar directamente la identidad del perfil: STS no está incluido. El operador externo necesita permiso de iniciar sesiones SSM. Ninguno de esos permisos se concede a nodos/aplicaciones.

Para consultar secreto RDS, otorgar al operador `secretsmanager:GetSecretValue` sobre **DatabaseAdminSecretArn**, no sobre todos los secretos. La plantilla solo crea conectividad; no inventa identidad del operador ni perfil EC2. Sin ese host no habrá acceso kubectl desde tu laptop al endpoint privado. SSM moderno usa `ssmmessages`; no depender de descarga de paquetes o actualizaciones del agente desde Internet. No se habilitan SSH ni puertos entrantes para administración. Endpoint `eks` solo permite DescribeCluster: administrar addons desde consola externa; ampliar su política con acciones/recursos concretos si después se necesita hacerlo desde ese host.

## Creación manual y bootstrap

1. Revisar parámetros reales: región, nombre estable, prefix list S3, ARN admin, versiones EKS/AMI/addons y versión/familia PostgreSQL. Las versiones deben ser compatibles y estar en soporte estándar; no se reutiliza evidencia antigua como validación actual.
2. Crear recursos de 01: subredes/tablas/asociaciones, SG/reglas y endpoints. Esperar endpoints disponibles. No crear aplicaciones todavía.
3. Preparar host admin privado y acceso SSM. Crear recursos 02: clúster, Access Entry, IAM, Pod Identity Agent, VPC CNI y kube-proxy; después NG; finalmente CoreDNS. Para bootstrap inicial, usar **NetworkPolicyMode=standard**, sin aplicaciones. Los agentes CNI/Pod Identity usan hostNetwork y arrancan antes de los pods normales. Usar AMI administrada AL2023 sin ImageId ni userdata personalizados: EKS aporta configuración de arranque.
4. Desde host admin aplicar `system-network-policy.yaml`. En consola EKS, cambiar configuración de addon VPC CNI a `enableNetworkPolicy: "true"` y `env.NETWORK_POLICY_ENFORCING_MODE: "strict"` (valor final en 02). Esperar rollout de `aws-node`; reiniciar CoreDNS y verificar DNS/API funcionando. Si se usa CloudFormation para 02, crear primero con `standard` y actualizar después a `strict`; una creación directa en `strict` puede dejar CoreDNS esperando la política. **No iniciar aplicaciones antes de completar este paso.**
5. Crear recursos 03. Esperar instancia, buckets, colas, state machine y tres asociaciones Pod Identity. El mismo rol del micro se asigna a cada réplica de su ServiceAccount; no se asigna a nivel NG.
6. Preparar PostgreSQL como indica sección siguiente. Publicar tu imagen desde una máquina con Internet a ECR `us-east-1`, `linux/amd64`, y copiar digest real. Los nodos no pueden descargar desde Docker Hub/GHCR ni construir imágenes.
7. Crear namespaces/ServiceAccounts/ConfigMaps/Services de `demo.yaml` primero, sin los Deployments. Aplicar `network-policies.yaml`; crear CA y Secrets por namespace. Comprobar estado del addon y políticas.
8. Completar copia local de `parameters/workload.example.json`; renderizar mediante `python3 aws/scripts/render.py /ruta/values.json /tmp/micro-lab.yaml`. Usa solo valores públicos: no poner contraseñas allí. Revisar archivo y aplicar Deployments al final, manualmente. Las tres imágenes renderizadas deben ser idénticas y estar fijadas por digest.
9. Verificar matriz de aceptación. No interpretar que tres pods `Running` prueban aislamiento.

No hay script que despliegue estos recursos. YAML es fuente final; las entradas `REQUIRED_*` fallan deliberadamente antes de cualquier uso real. `render.py` solo escribe un archivo local y rechaza imágenes externas/tags sin digest.

## PostgreSQL y arranque del micro

RDS **PostgreSQL Single-AZ `db.t4g.micro`, 20 GiB gp3 cifrados**, DB subnet group en ambas AZ, sin acceso público. Selección económica de laboratorio, no promesa de menor precio ni alta disponibilidad. Backup automático de un día; snapshot al borrar/reemplazar. Snapshots, buckets y ECR retenidos siguen generando cargos. Versión/familia obligatorias; soporte extendido deshabilitado. Confirmar disponibilidad de combinación antes de crear.

El secreto maestro se administra en RDS/Secrets Manager. No montar credenciales maestras en pods. Desde host admin con TLS `verify-full`, ejecutar [sql/init.sql](sql/init.sql) una vez conectado a base `postgres` como `lab_admin`; `\password` solicita contraseñas sin incluirlas en SQL. Se crean bases y usuarios `lab_general`, `lab_terceros`, `lab_onprem`, se revoca conexión pública y se carga el `schema.sql` existente en cada base. Cada login obtiene solo CONNECT, USAGE de esquemas, SELECT/INSERT/UPDATE. Los pods usan `SPRING_SQL_INIT_MODE=never`; nuevas migraciones requieren operador, no permisos DDL en runtime.

Cada namespace requiere:

- Secret `database-credentials`, claves `username` y `password`, de su login. No se incluye YAML con contraseñas; no usar el mismo login entre roles. Crear desde archivo temporal protegido o gestor de secretos, sin poner contraseña en historial ni Git; eliminar archivo después.
- ConfigMap `rds-ca`, clave `us-east-1-bundle.pem`, con CA regional oficial obtenida previamente desde máquina conectada. Se monta en `/etc/rds-ca/`; verificar fecha y rotación del bundle.

La [CA de RDS y verificación de TLS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html) son necesarias para `sslmode=verify-full`. S3 se crea antes de pods: el micro hace HeadBucket al iniciar, autorizado con `s3:ListBucket`; `CreateBucket` no está permitido. Cada rol usa bucket propio y solo objetos `sales/*`. Las URL prefirmadas funcionan únicamente desde un cliente que atraviese el gateway S3 de esta VPC, por política de bucket; no serán enlaces públicos útiles desde tu laptop.

Bases y buckets distintos significan ventas/datos distintos entre roles. No es un único workflow transaccional repartido entre las tres réplicas. Si después se requiere compartir datos o dividir endpoints por rol, hará falta definir contrato y permisos: el micro actual expone todos sus endpoints en los tres roles.

## SQS y Step Functions

`general` tiene permiso de iniciar `${ClusterName}-dispatch`. State machine **STANDARD** publica JSON recibido en `${ClusterName}-work` y termina al confirmar encolado. Su rol solo tiene `sqs:SendMessage` sobre esa cola. La integración administrada ocurre fuera de VPC; política de cola permite ese rol sin exigir SourceVpce al servicio Step Functions. Consumidor autorizado `terceros` sí debe pasar por endpoint privado SQS. `onprem` no tiene permisos de cola ni de state machine.

SQS Standard: visibilidad 120 s, long polling 20 s, retención 4 días; DLQ 14 días y `maxReceiveCount=5`. DLQ solo admite redrive desde cola de trabajo. Cifrado SSE-SQS y TLS obligatorio. Son valores explícitos de laboratorio, revisables según duración real. Reintentos de envío pueden duplicar mensajes; consumidor debe ser idempotente. Error al encolar no llega automáticamente a DLQ: la DLQ actúa sobre mensajes recibidos y no borrados. Redrive operativo necesita política/rol explícito adicional; no se concede a pods.

**La imagen actual no implementa estas llamadas ni consumo de cola.** Por eso no se incluye un worker, callback ficticio ni conexión HTTP inventada desde Step Functions al Service privado. La definición [ASL](../statemachine/docs-orchestrator.asl.json) refleja únicamente dispatcher. Finalizar una ejecución significa “encolado”, no “venta procesada”. Probar APIs AWS con herramienta de diagnóstico temporal bajo ServiceAccount correcto, o implementar luego integración en esta misma imagen; no conceder credenciales de nodos para simularlo.

## Validación

Desde raíz del repositorio, validación local sin AWS:

```bash
python3 -m venv /tmp/micro-lab-tools
/tmp/micro-lab-tools/bin/pip install PyYAML==6.0.3 cfn-lint==1.58.0
/tmp/micro-lab-tools/bin/python aws/scripts/validate.py
/tmp/micro-lab-tools/bin/cfn-lint -r us-east-1 -t aws/cloudformation/*.yaml --ignore-checks W3010
```

W3010 se omite porque AZ fijas son requisito. Validador revisa duplicados YAML, referencias, 45 exports/imports, ciclos, CIDR, asociaciones, endpoints, SG, NG, IAM, políticas, imagen/configuración común y ASL. Incluye casos negativos para exposición pública y renderer. No valida ejecución real ni es un demostrador formal de IAM/red.

Preflight **opcional**, de lectura, después de completar copia local de los tres JSON:

```bash
python3 aws/scripts/preflight.py --parameters-dir /ruta/parametros --report /tmp/lab-compatibility.json
```

Consulta catálogos EKS/addons/AMI, EC2, endpoints, prefix list S3 y opciones RDS; no crea recursos. No sustituye cuotas, SCP, autorización efectiva ni pruebas de runtime. No se ejecutó contra tu cuenta en esta revisión.

Pruebas manuales posteriores:

| Prueba | Resultado esperado |
|---|---|
| Pods/NG y digest | Cada namespace en NG correspondiente; mismo digest en tres roles |
| Logs/health | Sin errores JDBC, HeadBucket, CA o Pod Identity; probes 200 |
| general → terceros/onprem:8080 | Permitido |
| terceros → onprem/general:8080, onprem → general/terceros:8080 | Bloqueado |
| Pod de cada rol → su DB/bucket | Permitido |
| Login de un rol → base de otro; IAM → bucket ajeno | Denegado |
| general inicia dispatcher; terceros lee su mensaje | Permitido por IAM; requiere cliente que implemente llamadas |
| terceros/onprem inician dispatcher; general lee cola | AccessDenied |
| Repetir recepción sin borrar hasta maxReceiveCount | Mensaje termina en DLQ |
| Pod → IMDS `169.254.169.254`, destino externo no S3 | Bloqueado |
| Reiniciar pod y CoreDNS con CNI strict | Arrancan con permisos necesarios y sin ventana allow-all |

Usar clientes instalados en imagen de diagnóstico privada previamente copiada a ECR, solo de forma temporal. Nunca validar con rol admin en lugar del ServiceAccount del pod. `kubectl port-forward` es útil para probar el micro desde host admin, pero **no demuestra** cumplimiento de NetworkPolicy; pruebas de aislamiento deben salir de pods normales.

Fuentes: [EKS sin Internet](https://docs.aws.amazon.com/eks/latest/userguide/private-clusters.html), [CNI estricto y CoreDNS](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html), [Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html), [integración Step Functions/SQS](https://docs.aws.amazon.com/step-functions/latest/dg/connect-sqs.html).
