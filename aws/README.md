# Laboratorio demo-seguros

Infraestructura reproducible para `us-east-1`, con `us-east-1a` y `us-east-1b`, sin NAT Gateway. Las cuatro plantillas, parámetros, manifiestos y diagrama son editables. **No se ha desplegado infraestructura AWS.** Las validaciones contra AWS fueron consultas de lectura.

El camino de prueba es `cliente HTTPS con IAM → HTTP API Gateway → VPC Link V2 → NLB interno → pods nginx`. nginx devuelve JSON y `/healthz`; permite comprobar la infraestructura sin depender de S3, base de datos o lógica de negocio del simulador Java. Los endpoints de `workflow.http` todavía no están desplegados: se describe su integración futura en [future-stacks.md](future-stacks.md).

Para aplicar los YAML uno a uno, comienza por [Preparación y validación](#preparación-y-validación) y sigue [Despliegue paso a paso](#despliegue-paso-a-paso). Cada stack tiene su propio comando y comprobación antes de continuar. Todos los comandos parten de la **raíz del repositorio**, donde está la carpeta `aws/`; si tu terminal está dentro de `aws/`, ejecuta `cd ..` primero.

## Archivos y orden

| Archivo                                                                                            | Responsabilidad                                                                   |
| -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| [01-network.yaml](cloudformation/01-network.yaml)                                                   | VPC, seis subredes, IGW, cinco tablas, seis asociaciones y siete endpoints        |
| [02-eks.yaml](cloudformation/02-eks.yaml)                                                           | EKS, dos grupos de nodos, IAM, administrador, addons, logs y dos repositorios ECR |
| [03-ingress.yaml](cloudformation/03-ingress.yaml)                                                   | NLB, listener, target group, SG y Pod Identity del controlador                    |
| [04-api-gateway.yaml](cloudformation/04-api-gateway.yaml)                                           | HTTP API, autorización IAM, VPC Link e integración                              |
| [parameters/](parameters/)                                                                          | Un JSON por stack; IP y ARN del administrador son obligatorios                    |
| [controller-values.yaml](helm/controller-values.yaml)                                               | Valores Helm del AWS Load Balancer Controller                                     |
| [demo.yaml](kubernetes/demo.yaml), [target-group-binding.yaml](kubernetes/target-group-binding.yaml) | Servicio de prueba y enlace al target group existente                             |
| [network.mmd](diagrams/network.mmd), [network.svg](diagrams/network.svg)                             | Diagrama editable y vectorial                                                     |
| [addressing.csv](diagrams/addressing.csv)                                                           | Tabla editable de direccionamiento                                                |
| [scripts/](scripts/), [validation/](validation/)                                                     | Verificación local, preflight AWS y evidencia de compatibilidad                  |

Dependencias: `01 → 02 → 03 → 04`; además, `03` y `04` importan la red. Esperar a que termine cada stack. Después de `03`, copiar imágenes, instalar Helm y aplicar manifiestos; crear `04` cuando los targets estén sanos. CloudFormation por sí solo no instala el controlador ni registra pods.

Se reutilizó la plantilla de red existente, conservando logical IDs, CIDR y nombres de sus 17 exports. Se agregaron endpoints y dos exports. `02` importa el nombre de clúster definido por `01`, evitando diferencias entre tags y clúster. Todos los exports usan `${AWS::StackName}-...`. El README raíz y código Java existente no se modificaron. Si la red anterior ya estuviera desplegada, conservar su nombre de clúster explícitamente al actualizar; revisar el change set antes de ejecutar.

## Decisiones de arquitectura

**EKS 1.35 y AL2023 x86_64.** Verificados el 28 de octubre de 2026 en la API de EKS y SSM de `us-east-1`: soporte estándar hasta el 27 de marzo de 2027. Nodos `AL2023_x86_64_STANDARD`, `t3.medium`, 20 GiB gp3 cifrados por nodo; un grupo por AZ garantiza un nodo en cada zona. Escalado inicial 1/1/2 por grupo; no se instala autoscaler. `MaxSize` no provoca escalado automático. La política `STANDARD` evita contratar soporte extendido; planificar actualizaciones antes de su vencimiento. [Ciclo de versiones EKS](https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html), [AMI optimizadas](https://docs.aws.amazon.com/eks/latest/userguide/eks-optimized-ami.html).

| Elemento                             | Versión fijada y verificada |
| ------------------------------------ | ---------------------------- |
| Kubernetes                           | 1.35                         |
| Release de nodo                      | 1.35.9-20261026              |
| VPC CNI                              | v1.22.4-eksbuild.8           |
| kube-proxy                           | v1.35.3-eksbuild.59          |
| CoreDNS                              | v1.13.2-eksbuild.59          |
| EKS Pod Identity Agent               | v1.4.1-eksbuild.3            |
| AWS Load Balancer Controller / chart | v3.6.2 / 3.6.2               |

[aws-compatibility.json](validation/aws-compatibility.json) registra compatibilidad `amd64`/`EC2`, disponibilidad de AMI y endpoints. El preflight vuelve a consultar AWS: no asumir que estas versiones siguen disponibles meses después. Los addons básicos iniciales permiten el bootstrap de nodos; después CloudFormation los adopta con `ResolveConflicts: OVERWRITE`. CoreDNS espera a nodos, CNI y kube-proxy. No se agrega EBS CSI: este ejemplo no usa PVC.

**Administración explícita.** Endpoint Kubernetes privado habilitado para nodos. Endpoint público habilitado únicamente para `AdminPublicCidr`, IPv4 `/32`. `AdminPrincipalArn` crea una Access Entry con `AmazonEKSClusterAdminPolicy`; no se concede administración implícita al creador. Debe ser un rol IAM o usuario persistente, nunca `arn:aws:sts:...:assumed-role/...`. La plantilla rechaza `/0`, y el preflight también rechaza IP privadas o inválidas. El usuario que ejecute kubectl debe utilizar ese principal; el rol necesita confianza y permisos para ser asumido, y `eks:DescribeCluster`. La autorización Kubernetes y los permisos IAM de despliegue son distintos.

**Propiedad del ingreso.** CloudFormation controla NLB, listener TCP 80, target group IP:8080 y SG. Kubernetes usa `Service: ClusterIP` y `TargetGroupBinding`: solo registra/desregistra IP de pods. El rol del controlador no permite crear/borrar balanceadores ni modificar SG; sus únicas escrituras son RegisterTargets/DeregisterTargets sobre ese target group. No cambiar el Service a `LoadBalancer` ni agregar Ingress esperando que cree infraestructura. [TargetGroupBinding](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/targetgroupbinding/targetgroupbinding/).

**Seguridad y tráfico.** SG de VPC Link permite salida TCP 80 solo al SG del NLB; SG del NLB acepta ese origen y sale TCP 8080 al SG de nodos/pods. Health check HTTP `/healthz`. VPC CNI asigna IP de las subredes EKS a los pods y hereda SG de los nodos; no se habilita Security Groups for Pods. El SG administrado por EKS conserva comunicación interna, incluido webhook 9443, kubelet y DNS. Su salida predeterminada amplia no crea una ruta a Internet. La red no sustituye autorización IAM. TLS termina en API Gateway; el tramo privado al backend usa HTTP. Para datos reales, evaluar TLS interno y requisitos de cumplimiento. [SG de EKS](https://docs.aws.amazon.com/eks/latest/userguide/sec-group-reqs.html).

HTTP API es un servicio regional con URL pública e integración privada; no es una API privada. Todas sus rutas requieren `AWS_IAM`. El VPC Link usa ENI privadas en ambas AZ y el ARN del **listener**, no el ARN ni DNS del NLB. Se elimina el prefijo de stage con `overwrite:path`. Cross-zone habilitado en NLB; puede generar tráfico entre AZ. [Integraciones privadas HTTP API](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-private.html).

## Conectividad sin NAT

| Endpoint | Tipo / ubicación | Uso y alcance |
| ------------------------ | ------------------------------------- | ------------------------------------------------------------------------ |
| `ec2` | Interface, una ENI en cada subred EKS | API EC2 para VPC CNI y consultas del controlador |
| `ecr.api` | Interface, ambas AZ | Autenticación y metadatos de imágenes |
| `ecr.dkr` | Interface, ambas AZ | Descarga desde registry ECR privado |
| `eks` | Interface, ambas AZ | DescribeCluster; API de administración EKS, **no** API Kubernetes |
| `eks-auth` | Interface, ambas AZ | Credenciales de Pod Identity para el controlador |
| `elasticloadbalancing` | Interface, ambas AZ | Consultar y reconciliar targets; no transporta peticiones de usuarios |
| `s3` | Gateway asociado a RT EKS A/B | `GetObject` solo en bucket regional de capas ECR |

Los seis endpoints Interface tienen DNS privado y SG con entrada HTTPS 443 únicamente desde los dos CIDR EKS. Políticas limitadas a acciones utilizadas y principales de esta cuenta; IAM sigue siendo obligatorio. ECR permite leer imágenes de los repositorios administrados por AWS para addons: no se restringe el recurso a repositorios de nuestra cuenta. S3 permite exclusivamente `prod-us-east-1-starport-layer-bucket/*`; su principal debe permanecer compatible con las descargas de capas servidas por ECR. El export `PrivateConnectivityReady` referencia todos los endpoints; `02` lo importa. Crear `01` completamente antes de nodos evita arranque sin conectividad. [EKS sin Internet](https://docs.aws.amazon.com/eks/latest/userguide/private-clusters.html), [endpoints ECR](https://docs.aws.amazon.com/AmazonECR/latest/userguide/vpc-endpoints.html).

No se incluyen STS ni OIDC: este laboratorio usa Pod Identity en la misma cuenta, no IRSA ni AssumeRole desde pods. El agente usa `eks-auth`; AWS realiza la asunción del rol por su servicio. No se incluyen Logs para pods: los logs del control plane los envía EKS, con retención de siete días. `kubectl logs` funciona por la API Kubernetes. Si se añade Fluent Bit, IRSA, SSM, Secrets Manager, SQS o Step Functions desde pods, agregar sus endpoints regionales, políticas IAM y reglas antes de utilizarlos. WAF/Shield quedan desactivados en Helm para evitar llamadas externas. [Pod Identity Agent](https://docs.aws.amazon.com/eks/latest/userguide/pod-id-agent-setup.html), [controlador en clúster aislado](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/deploy/installation/).

Docker Hub, GHCR, paquetes de sistemas operativos y otras URL externas no son alcanzables desde nodos. Copiar previamente las imágenes desde una máquina con Internet a ECR privado en `us-east-1`. No depender del primer pull-through cache sin salida externa. Los endpoints creados no incluyen ECR Public; el controlador se copia también a ECR privado. Para la aplicación Java futura, ampliar la política S3 al bucket de documentos y otorgar Pod Identity propio; el gateway actual no autoriza documentos de negocio.

El rol del nodo incluye `AmazonEKS_CNI_Policy` para simplificar bootstrap. IMDSv2 obligatorio y hop limit 1 evitan acceso ordinario de pods al rol del nodo; no son aislamiento frente a pods privilegiados/hostNetwork. Separar permisos CNI y controles de admisión antes de producción.

## Direccionamiento

AWS reserva cinco IPv4 por subred: `/24` ofrece **251**, `/20` ofrece **4091** antes de asignar ENI, nodos, pods y balanceadores. No son IP libres garantizadas ni capacidad de pods por nodo. El límite de pods depende también de ENI y tipo EC2. `172.20.0.0/16` es CIDR virtual de Services Kubernetes; no se solapa con la VPC y no es una séptima subred AWS.

| Componente                      | AZ                             | Subred                 | CIDR                         | IP utilizables                               | Tabla de rutas                | Destino                           | Propósito                               |
| ------------------------------- | ------------------------------ | ---------------------- | ---------------------------- | -------------------------------------------- | ----------------------------- | --------------------------------- | ---------------------------------------- |
| VPC                             | us-east-1 (regional)           | Contiene seis subredes | 10.20.0.0/16                 | Se calcula por subred                        | Cinco tablas explícitas      | 10.20.0.0/16 → local             | DNS habilitado; red del laboratorio      |
| Pública                        | us-east-1a                     | PublicSubnetA          | 10.20.0.0/24                 | 251                                          | PublicRouteTable              | local; 0.0.0.0/0 → IGW           | Reserva pública, sin cargas             |
| EKS privada                     | us-east-1a                     | EKSPrivateSubnetA      | 10.20.16.0/20                | 4091                                         | EKSPrivateRouteTableA         | local; prefix list S3 → gateway  | Nodos, pods y ENI privadas               |
| Datos privada                   | us-east-1a                     | DataSubnetA            | 10.20.64.0/24                | 251                                          | DataRouteTableA               | 10.20.0.0/16 → local             | Reserva para base futura                 |
| Pública                        | us-east-1b                     | PublicSubnetB          | 10.20.1.0/24                 | 251                                          | PublicRouteTable              | local; 0.0.0.0/0 → IGW           | Reserva pública, sin cargas             |
| EKS privada                     | us-east-1b                     | EKSPrivateSubnetB      | 10.20.32.0/20                | 4091                                         | EKSPrivateRouteTableB         | local; prefix list S3 → gateway  | Nodos, pods y ENI privadas               |
| Datos privada                   | us-east-1b                     | DataSubnetB            | 10.20.65.0/24                | 251                                          | DataRouteTableB               | 10.20.0.0/16 → local             | Reserva para base futura                 |
| Nodos/pods EKS                  | a y b                          | EKS A/B                | 10.20.16.0/20; 10.20.32.0/20 | Dinámicas; comparten pool anterior          | RT EKS A/B                    | API privada, endpoints y NLB      | Cómputo y demo                          |
| NLB interno                     | a y b                          | EKS A/B                | Mismos CIDR EKS              | Dinámicas; comparten pool EKS               | RT EKS A/B                    | IP de pods:8080                   | Ingreso interno; DNS generado            |
| VPC Link ENI                    | a y b                          | EKS A/B                | Mismos CIDR EKS              | Dinámicas; comparten pool EKS               | RT EKS A/B                    | NLB:80 por ruta local             | Integración HTTP API                    |
| Seis endpoints Interface        | a y b                          | EKS A/B                | Mismos CIDR EKS              | 6 ENI por AZ; IP dinámicas del pool EKS     | RT EKS A/B                    | Servicios AWS:443 por PrivateLink | ECR api/dkr, EC2, EKS, EKS Auth, ELB     |
| ENI privadas control plane      | a y b                          | EKS A/B                | Mismos CIDR EKS              | Dinámicas; asignación administrada por EKS | RT EKS A/B                    | API privada y nodos               | Conexión al control plane externo       |
| Gateway endpoint S3             | Regional                       | No ocupa subred        | Prefix list regional S3      | No consume IP de subred                      | RT EKS A/B                    | Bucket de capas ECR               | Descarga privada de imágenes            |
| IGW                             | Regional/VPC                   | No ocupa subred        | No aplica                    | No aplica                                    | PublicRouteTable              | Internet                          | Salida únicamente de subredes públicas |
| API Gateway / control plane     | Regional, fuera de VPC cliente | No aplica              | Administrado por AWS         | No asignadas por usuario                     | No aplica                     | VPC Link / ENI EKS                | Servicios administrados                  |
| Futuro PostgreSQL               | Por definir; producción a y b | Datos A/B reservadas   | 10.20.64.0/24; 10.20.65.0/24 | Sin asignación; no creado                   | RT datos A/B                  | local, TCP 5432 con SG            | DNS privado del motor futuro             |
| Futuro SQS/DLQ + Step Functions | Regional                       | Fuera de VPC cliente   | No aplica                    | No aplica                                    | Por definir endpoints futuros | API regional                      | Mensajería y orquestación pendientes   |

Las tablas privadas no tienen ruta por defecto. Todas incluyen `10.20.0.0/16 → local`; únicamente EKS A/B agregan ruta al prefix list regional S3. Endpoints Interface usan ruta local hacia sus ENI, sin nuevas rutas. Tablas separadas no aíslan capas: los SG controlan el acceso. No se fijan IP para nodos, pods, NLB, endpoints ni VPC Link. Se consumen IDs, DNS y ARN generados por AWS.

## Preparación y validación

Ejecutar desde la raíz del repositorio. Requisitos: AWS CLI v2 con credenciales, Python 3 con PyYAML, `cfn-lint`, Docker con soporte `linux/amd64`, Helm 3 y kubectl compatible con Kubernetes 1.35. La máquina administradora necesita Internet y debe salir por la IPv4 `/32` configurada. No se necesita ejecutar comandos desde nodos.

```bash
python3 -m venv /tmp/demo-seguros-tools
/tmp/demo-seguros-tools/bin/pip install PyYAML==6.0.3 cfn-lint==1.58.0
/tmp/demo-seguros-tools/bin/python aws/scripts/validate.py
/tmp/demo-seguros-tools/bin/cfn-lint -r us-east-1 -t aws/cloudformation/*.yaml --ignore-checks W3010
```

Se omite únicamente W3010 porque las AZ fijas son requisito explícito. El validador comprueba YAML sin claves duplicadas, JSON, referencias, imports/exports, ciclos, CIDR exactos, rutas, endpoints y contrato entre Service y TargetGroupBinding. No valida permisos efectivos de SCP, cuotas o disponibilidad de EC2.

La IP pública `/32` y el ARN IAM se configuran en el paso 2, antes de EKS. Puedes crear primero la red sin esos datos. Los parámetros administrativos locales quedan ignorados por Git.

Permisos de despliegue: CloudFormation, EC2/VPC, EKS, IAM con `iam:PassRole`, ECR, Logs, ELB y API Gateway, además de creación de roles vinculados al servicio donde corresponda. `CAPABILITY_IAM` reconoce creación de roles; no otorga permisos. Comprobar cuotas para dos nodos y capacidad de `t3.medium` en ambas AZ. No se asignan nombres IAM globales. Todo debe estar en la misma cuenta y región.

Validación AWS opcional, sin despliegue:

```bash
for template in aws/cloudformation/*.yaml; do
  aws cloudformation validate-template --region us-east-1 --template-body "file://$template"
done
```

## Despliegue paso a paso

**Los comandos siguientes crean recursos facturables. No fueron ejecutados durante la preparación.** No hay script que despliegue automáticamente todo el laboratorio. Ejecutar bloques en orden y detenerse ante errores. Para revisar primero un change set, agregar `--no-execute-changeset` al comando correspondiente; aun así crea un change set, y debe ejecutarse antes del siguiente stack.

Orden: **0. Terminal → 1. Red → 2. EKS → 3. Ingreso → 3.1. Imágenes → 3.2. Kubernetes → 4. API Gateway**. Los YAML de `cloudformation/` se ejecutan con AWS CLI; los de `kubernetes/`, con kubectl después de sustituir sus parámetros. `helm/controller-values.yaml` es un archivo de valores para Helm.

`aws cloudformation deploy` espera hasta que termine la operación. No ejecutes el siguiente paso en otra terminal mientras el anterior siga en curso. Tras cada despliegue, verifica `CREATE_COMPLETE` o `UPDATE_COMPLETE`; estados `*_IN_PROGRESS`, `*_FAILED` o `*ROLLBACK*` no permiten continuar. Si el stack ya existe, el mismo comando propone una actualización; sin cambios, puede indicar que no hay nada que actualizar.

### Paso 0 — Preparar la terminal

Mantén la misma terminal durante el recorrido: los pasos posteriores utilizan variables y la función `output` definida en el paso 3.1. Si usas un perfil específico, configura `AWS_PROFILE` antes de verificar la identidad.

```bash
# Opcional: export AWS_PROFILE=nombre-de-tu-perfil
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export AWS_PAGER=''

aws sts get-caller-identity --region us-east-1
```

Comprueba que `Account` y `Arn` pertenecen a la cuenta y al principal con los que quieres desplegar. Si falla la autenticación, configura o renueva tus credenciales antes de seguir. El ARN STS de una sesión no debe copiarse como `AdminPrincipalArn`.

### Paso 1 — Crear la red: 01-network.yaml

Revisa `aws/parameters/01-network.json`: `EKSClusterName` debe conservar el nombre elegido para el clúster. Este stack crea también los endpoints privados, que empiezan a facturar aunque todavía no existan nodos.

```bash
aws cloudformation deploy --region us-east-1 \
  --stack-name demo-seguros-networking --template-file aws/cloudformation/01-network.yaml \
  --parameter-overrides file://aws/parameters/01-network.json --tags Project=demo-seguros
```

Antes de avanzar, consulta estado y exports disponibles:

```bash
aws cloudformation describe-stacks --region us-east-1 --stack-name demo-seguros-networking \
  --query 'Stacks[0].{Estado:StackStatus,Salidas:Outputs}' --output json
```

**Continuar únicamente con estado correcto y salidas `VpcId`, `EKSPrivateSubnetAId`, `EKSPrivateSubnetBId` y `PrivateConnectivityReady`.** No necesitas copiar sus IDs a la siguiente plantilla: se importan mediante el nombre del stack.

### Paso 2 — Crear el clúster y nodos: 02-eks.yaml

Crea una copia local de parámetros. Si ya existe y contiene tus datos, edítala sin volver a copiar encima:

```bash
cp -n aws/parameters/02-eks.json aws/parameters/02-eks.local.json
```

Edita `aws/parameters/02-eks.local.json` antes de ejecutar el siguiente comando:

| Parámetro | Valor que debes proporcionar |
| --------------------- | ---------------------------------------------------------------------------------------------- |
| `NetworkStackName` | `demo-seguros-networking`, o el nombre real del paso 1 |
| `AdminPublicCidr` | Tu IPv4 pública de salida seguida de `/32`; no la IP local de tu computadora |
| `AdminPrincipalArn` | ARN IAM de usuario o rol que utilizarás para administrar Kubernetes; no un ARN STS de sesión |

Los demás parámetros ya incluyen las versiones fijadas del laboratorio. Compruébalas antes de crear EKS:

```bash
python3 aws/scripts/preflight.py --parameters aws/parameters/02-eks.local.json \
  --report /tmp/demo-seguros-preflight.json
```

El preflight solo consulta AWS y valida los parámetros; no crea recursos. Si falla, corrige el motivo antes de continuar. Si la AMI recomendada cambió, revisa y actualiza la release fijada; no omitas la comprobación. `--compatibility-only` no sustituye este paso completo.

```bash
aws cloudformation deploy --region us-east-1 \
  --stack-name demo-seguros-eks --template-file aws/cloudformation/02-eks.yaml \
  --parameter-overrides file://aws/parameters/02-eks.json \
  --capabilities CAPABILITY_IAM --tags Project=demo-seguros
```

Comprueba el stack antes de configurar kubectl:

```bash
aws cloudformation describe-stacks --region us-east-1 --stack-name demo-seguros-eks \
  --query 'Stacks[0].{Estado:StackStatus,Salidas:Outputs}' --output json
```

Con estado correcto, configura kubectl con las credenciales del administrador. Para un rol asumible, agrega `--role-arn ARN_DEL_ROL_ADMIN` al comando `update-kubeconfig`; para un usuario IAM o sesión ya autenticada con el principal correcto, omite esa opción. Si cambiaste `EKSClusterName` en el paso 1, sustituye `demo-seguros` en el comando.

```bash
aws eks update-kubeconfig --region us-east-1 --name demo-seguros
kubectl get nodes -L topology.kubernetes.io/zone
kubectl get pods -n kube-system
```

**Continuar cuando ambos nodos estén `Ready`, uno por AZ, y los pods de addons estén en ejecución sin errores.** Si cambia tu IPv4 de salida, actualiza `AdminPublicCidr` en stack `02`; no amplíes a `/0`.

### Paso 3 — Crear el ingreso interno: 03-ingress.yaml

Revisa `aws/parameters/03-ingress.json`: `NetworkStackName` y `EksStackName` deben coincidir con los stacks anteriores.

```bash
aws cloudformation deploy --region us-east-1 \
  --stack-name demo-seguros-ingress --template-file aws/cloudformation/03-ingress.yaml \
  --parameter-overrides file://aws/parameters/03-ingress.json \
  --capabilities CAPABILITY_IAM --tags Project=demo-seguros
```

Comprueba estado y salidas:

```bash
aws cloudformation describe-stacks --region us-east-1 --stack-name demo-seguros-ingress \
  --query 'Stacks[0].{Estado:StackStatus,Salidas:Outputs}' --output json
```

**Continuar con estado correcto y salidas `ListenerArn`, `TargetGroupArn` y `VpcLinkSecurityGroupId`.** En este punto es normal que el target group no tenga destinos: todavía falta instalar el controlador y el servicio. Completa 3.1 y 3.2 antes de ejecutar `04-api-gateway.yaml`.

### Paso 3.1 — Copiar imágenes a ECR privado

Los siguientes comandos necesitan Docker, Internet y permisos de push ECR en la estación administradora. No se ejecutan dentro de EKS. [images.json](validation/images.json) conserva digests verificados; se copia explícitamente la variante amd64.

```bash
output() {
  aws cloudformation describe-stacks --region us-east-1 --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text
}
export DEMO_REPO="$(output demo-seguros-eks DemoRepositoryUri)"
export CONTROLLER_REPO="$(output demo-seguros-eks ControllerRepositoryUri)"
export VPC_ID="$(output demo-seguros-networking VpcId)"
export TARGET_GROUP_ARN="$(output demo-seguros-ingress TargetGroupArn)"
export CLUSTER_NAME="$(output demo-seguros-eks ClusterName)"

aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin "${DEMO_REPO%%/*}"

DEMO_SOURCE='docker.io/library/nginx@sha256:0dcc88822d45581e65ae329f8be769762bf628d3b2bb7d2a077d4aa5c98b30e3'
CONTROLLER_SOURCE='public.ecr.aws/eks/aws-load-balancer-controller@sha256:a2caa8b59970a4a42519a384caaeee2e36b25063878c3621708e71e88297c9f2'
docker pull --platform linux/amd64 "$DEMO_SOURCE"
docker tag "$DEMO_SOURCE" "$DEMO_REPO:lab-1"
docker push "$DEMO_REPO:lab-1"
docker pull --platform linux/amd64 "$CONTROLLER_SOURCE"
docker tag "$CONTROLLER_SOURCE" "$CONTROLLER_REPO:v3.6.2"
docker push "$CONTROLLER_REPO:v3.6.2"
export DEMO_IMAGE="$DEMO_REPO:lab-1"
```

Repositorios usan tags inmutables. En una segunda ejecución, si las imágenes ya existen con el digest esperado, omitir push; no sobrescribir tags ni desactivar inmutabilidad. El mirror público de nginx devolvió rate limit durante una consulta: se verificó su fuente oficial Docker Hub. Si un registry limita descargas, autenticar Docker o reintentar después; no cambiar a una imagen no verificada.

### Paso 3.2 — Instalar controlador y aplicar YAML de Kubernetes

Descargar chart en la estación administradora, comprobar checksum y utilizarlo localmente. No requiere cert-manager; Helm genera certificado del webhook. Las CRD se instalan con la primera instalación. Para actualizaciones futuras, revisar cambios de CRD expresamente: Helm no las actualiza automáticamente.

```bash
mkdir -p aws/generated
curl -fsSL https://aws.github.io/eks-charts/aws-load-balancer-controller-3.6.2.tgz \
  -o aws/generated/aws-load-balancer-controller-3.6.2.tgz
python3 - <<'PY'
import hashlib
from pathlib import Path
p = Path('aws/generated/aws-load-balancer-controller-3.6.2.tgz')
assert hashlib.sha256(p.read_bytes()).hexdigest() == '9255bf39873b6cb31106dbe61dc87c3617bff4197783b5b94381811a13f99c3c'
PY
helm upgrade --install aws-load-balancer-controller \
  aws/generated/aws-load-balancer-controller-3.6.2.tgz \
  --namespace kube-system --values aws/helm/controller-values.yaml \
  --set-string clusterName="$CLUSTER_NAME" --set-string vpcId="$VPC_ID" \
  --set-string image.repository="$CONTROLLER_REPO" --set-string image.tag=v3.6.2 \
  --wait --timeout 10m

python3 - <<'PY'
import os
from pathlib import Path
for filename in ['demo.yaml', 'target-group-binding.yaml']:
    text = Path('aws/kubernetes', filename).read_text()
    for key in ['DEMO_IMAGE', 'TARGET_GROUP_ARN', 'VPC_ID']:
        text = text.replace('${' + key + '}', os.environ[key])
    assert '${' not in text, 'Falta sustituir un parametro'
    Path('aws/generated', filename).write_text(text)
PY
kubectl apply -f aws/generated/demo.yaml
kubectl rollout status deployment/demo -n demo-seguros --timeout=5m
kubectl apply -f aws/generated/target-group-binding.yaml
aws elbv2 describe-target-health --region us-east-1 --target-group-arn "$TARGET_GROUP_ARN"
```

Esperar a ver **dos IP registradas y estado `healthy`**; una lista vacía no es éxito. Comprobar `kubectl get pods -n demo-seguros -o wide` y `kubectl logs -n kube-system deployment/aws-load-balancer-controller` ante errores. Los health checks pueden tardar minutos después del rollout.

### Paso 4 — Crear API Gateway: 04-api-gateway.yaml

Requisito: las dos IP del target group están `healthy`. Revisa `aws/parameters/04-api-gateway.json`: `NetworkStackName` e `IngressStackName` deben coincidir con los stacks ya creados.

```bash
aws cloudformation deploy --region us-east-1 \
  --stack-name demo-seguros-api --template-file aws/cloudformation/04-api-gateway.yaml \
  --parameter-overrides file://aws/parameters/04-api-gateway.json --tags Project=demo-seguros
```

Comprueba estado antes de probar la API:

```bash
aws cloudformation describe-stacks --region us-east-1 --stack-name demo-seguros-api \
  --query 'Stacks[0].{Estado:StackStatus,Salidas:Outputs}' --output json
```

Con estado correcto, recupera la URL y el ARN de invocación. La función `output` debe estar definida en tu terminal desde el paso 3.1:

```bash
export API_URL="$(output demo-seguros-api ApiEndpoint)"
output demo-seguros-api InvokeResourceArn
```

El principal de prueba necesita `execute-api:Invoke` sobre el ARN anterior. Ser administrador Kubernetes no otorga ese permiso. La petición sin firma debe recibir 403. Para una prueba firmada con el perfil AWS CLI actual y curl con soporte SigV4, ejecutar este bloque. Las credenciales se pasan por stdin a curl, sin imprimirlas ni guardarlas en archivo:

```bash
python3 - <<'PY'
import json, os, subprocess
credentials = json.loads(subprocess.check_output(
    ['aws', 'configure', 'export-credentials', '--format', 'process'], text=True))
config = 'user = ' + json.dumps(credentials['AccessKeyId'] + ':' + credentials['SecretAccessKey']) + '\n'
if credentials.get('SessionToken'):
    config += 'header = ' + json.dumps('x-amz-security-token: ' + credentials['SessionToken']) + '\n'
subprocess.run(['curl', '--config', '-', '--aws-sigv4', 'aws:amz:us-east-1:execute-api',
                '--fail-with-body', '--max-time', '30', os.environ['API_URL'] + '/healthz'],
               input=config, text=True, check=True)
PY
```

Respuesta esperada: `ok`. Para `/`, devuelve `{"proyecto":"demo-seguros","estado":"ok"}`. Probar después recreación de pods y verificar que el controlador reemplaza IP registradas sin tocar CloudFormation. Las pruebas contra Kubernetes y la API real quedan pendientes hasta desplegar.

### Si un paso falla o retomas otro día

No continúes con el siguiente YAML. Consulta los eventos del stack que falló; sustituye el nombre del ejemplo por el correspondiente:

```bash
aws cloudformation describe-stack-events --region us-east-1 --stack-name demo-seguros-networking \
  --query "StackEvents[?contains(ResourceStatus, 'FAILED')].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]" \
  --output table
```

Corrige la causa indicada por `ResourceStatusReason`. Un stack en `ROLLBACK_COMPLETE` después de una creación fallida normalmente necesita eliminarse antes de volver a crearlo; revisa sus recursos antes de hacerlo. No borres stacks sanos para reintentar uno posterior. Si cambias nombres de stacks, actualiza los parámetros consumidores: CloudFormation impide borrar o modificar exports en uso.

Para retomar, repite el paso 0 y consulta el estado del último stack aplicado. Conserva `02-eks.local.json`; no sobrescribas tu IP/ARN. Antes de usar Helm o kubectl otra vez, recupera las variables del paso 3.1 y configura kubeconfig si cambió tu sesión. No repitas los pushes de imágenes que ya existen con el digest esperado.

#### Recuperar el paso 2 tras el error de tags de EKS

La versión inicial de `02-eks.yaml` copiaba el export CSV `PrivateConnectivityReady` directamente al tag `PrivateConnectivity`. EKS rechaza las comas en tags. La plantilla corregida convierte comas a espacios con `Fn::Split` y `Fn::Join`, conservando la dependencia y el export de red. **No requiere modificar ni volver a desplegar `01-network.yaml`.** [Restricciones de tags EKS](https://docs.aws.amazon.com/eks/latest/userguide/eks-using-tags.html).

Si tu creación falló por este motivo, verifica primero que `demo-seguros-eks` esté en `ROLLBACK_COMPLETE` y revisa los recursos restantes:

```bash
aws cloudformation describe-stacks --region us-east-1 --stack-name demo-seguros-eks \
  --query 'Stacks[0].StackStatus' --output text
aws cloudformation list-stack-resources --region us-east-1 --stack-name demo-seguros-eks \
  --query 'StackResourceSummaries[].[LogicalResourceId,ResourceType,ResourceStatus,PhysicalResourceId]' \
  --output table
```

Solo para ese stack de creación fallida en `ROLLBACK_COMPLETE`, elimina el registro del stack y espera a que termine. Estos comandos son manuales; no eliminan el stack de red:

```bash
aws cloudformation delete-stack --region us-east-1 --stack-name demo-seguros-eks
aws cloudformation wait stack-delete-complete --region us-east-1 --stack-name demo-seguros-eks
```

Después repite el preflight y despliegue del paso 2 con la plantilla corregida y el archivo que contiene tus valores reales. Si los completaste en `aws/parameters/02-eks.json`, usa ese archivo en `--parameters` y `--parameter-overrides`; no lo sustituyas por una copia con placeholders.

El rollback puede dejar `DemoRepository` y `ControllerRepository` en `DELETE_SKIPPED` por `DeletionPolicy: Retain`. Conserva sus nombres antes de borrar el stack. El nuevo intento crea otros repositorios con nombres generados; los anteriores no se adoptan automáticamente y deben revisarse por separado. No se borran imágenes como parte de esta corrección.

## Costos y limitaciones

Estimación orientativa USD, `us-east-1`, 730 horas/mes, sin impuestos, descuentos ni Free Tier. Tarifas verificadas/consultadas el 28 de octubre de 2026; confirmar en AWS Pricing Calculator antes de mantenerlo encendido.

| Concepto                      | Cálculo                             | USD/mes aproximados |
| ----------------------------- | ------------------------------------ | ------------------: |
| EKS estándar                 | 1 × 0.10/h × 730                   |               73.00 |
| Dos t3.medium Linux On-Demand | 2 × 0.0416/h × 730                 |               60.74 |
| gp3                           | 40 GiB × 0.08/GB-mes                |                3.20 |
| Endpoints Interface           | 6 servicios × 2 AZ × 0.01/h × 730 |               87.60 |
| NLB                           | 0.0225/h × 730                      |               16.43 |
| **Base aproximada**     | Sin consumos variables               |    **240.97** |

Añadir NLCU (~0.006/NLCU-h), procesamiento PrivateLink (~0.01/GB primer tramo), solicitudes HTTP API, ECR, CloudWatch, transferencia y posibles créditos CPU de instancias T3 Unlimited. VPC, subredes, tablas, IGW y gateway S3 no tienen cargo fijo propio; no se crean NAT, EIP ni IPv4 públicas de EC2/NLB. El VPC Link de HTTP API no añade cargo fijo propio: se pagan API y recursos asociados. Esta base equivale aproximadamente a USD 0.33/h, sin variables; reducir duración del laboratorio evita más costo que cambiar pequeños tamaños.

Fuentes: [EKS](https://aws.amazon.com/eks/pricing/), [PrivateLink](https://aws.amazon.com/privatelink/pricing/), [ELB](https://aws.amazon.com/elasticloadbalancing/pricing/), [EC2](https://aws.amazon.com/ec2/pricing/on-demand/), [EBS](https://aws.amazon.com/ebs/pricing/), [API Gateway](https://aws.amazon.com/api-gateway/pricing/). Tarifa EC2 confirmada además por `pricing:GetProducts` para Linux/Shared/t3.medium/US East (N. Virginia).

No hay DB, SQS, Step Functions, autoscaler, persistencia de aplicación ni collector de logs de pods. EKS mantiene cobro aunque no existan pods. Detener nodos manualmente no detiene el clúster y el grupo administrado puede reemplazarlos. Retención de logs del control plane: siete días. Servicio nginx es prueba de infraestructura, sin datos reales ni alta disponibilidad contractual.

## Retirada manual

Guardar previamente resultados y URI de repositorios ECR. Borrar el TargetGroupBinding **mientras el controlador y el clúster siguen funcionando**; permite desregistrar targets y completar finalizers. Luego retirar servicio y Helm. No forzar finalizers como procedimiento normal.

```bash
kubectl delete -f aws/generated/target-group-binding.yaml --wait=true --timeout=5m
kubectl delete -f aws/generated/demo.yaml
helm uninstall aws-load-balancer-controller -n kube-system

aws cloudformation delete-stack --region us-east-1 --stack-name demo-seguros-api
aws cloudformation wait stack-delete-complete --region us-east-1 --stack-name demo-seguros-api
aws cloudformation delete-stack --region us-east-1 --stack-name demo-seguros-ingress
aws cloudformation wait stack-delete-complete --region us-east-1 --stack-name demo-seguros-ingress
aws cloudformation delete-stack --region us-east-1 --stack-name demo-seguros-eks
aws cloudformation wait stack-delete-complete --region us-east-1 --stack-name demo-seguros-eks
aws cloudformation delete-stack --region us-east-1 --stack-name demo-seguros-networking
aws cloudformation wait stack-delete-complete --region us-east-1 --stack-name demo-seguros-networking
```

`02` conserva ambos repositorios ECR mediante `Retain` para evitar borrar imágenes accidentalmente; siguen ocupando almacenamiento. Eliminarlos manualmente cuando sus imágenes no se necesiten. Logs, nodos y discos del stack sí se eliminan. Si queda un consumidor de exports o ENI en uso, resolverlo antes de repetir el borrado; no dejar endpoints facturando inadvertidamente.

## Validación realizada y reproducción del diagrama

Ver [validation/README.md](validation/README.md) para resultados y límites. Para regenerar SVG con la misma versión Mermaid CLI:

```bash
npx --yes --package @mermaid-js/mermaid-cli@12.0.0 mmdc \
  -i aws/diagrams/network.mmd -o aws/diagrams/network.svg -b white --size 2400
```

[network.svg](diagrams/network.svg) muestra recursos incluidos con líneas continuas y futuros con líneas discontinuas. API Gateway y control plane EKS aparecen fuera de la VPC del cliente; solo sus conexiones/ENI residen en las subredes privadas.
