# Laboratorio nuevo: práctica manual con base reproducible

Estas son las plantillas para la cuenta nueva. No son actualizaciones de los stacks de `aws/cloudformation/`: esos archivos describen el diseño anterior en us-east-1, mezclan RDS con servicios/EKS y su dispatcher encola mensajes, distinto del flujo actual. No combinar ambas series sobre los mismos recursos.

## Crear ahora desde la consola

1. Seleccionar la cuenta y región nuevas. En **EC2 → Subnets / Availability Zones**, consultar dos AZ disponibles y sus **Zone IDs**. No copiar las AZ de la cuenta anterior. Confirmar compatibilidad y disponibilidad de RDS PostgreSQL, clase y versión en esa región.
2. En **CloudFormation → Create stack → With new resources → Upload a template file**, cargar `01-network-general.yaml`. Nombre sugerido: `micro-test-network`. Introducir los dos Zone IDs distintos. `VpcSecondOctet=20` propone `10.20.0.0/16`; cambiarlo antes de crear si hay solapamiento. Esperar `CREATE_COMPLETE`.
3. Crear otro stack con `02-database.yaml`, nombre `micro-test-database`. `NetworkStackName` debe coincidir exactamente con el anterior. Introducir versión PostgreSQL disponible en soporte estándar y familia correspondiente, por ejemplo familia `postgres17` para versión 17.x; el ejemplo no confirma disponibilidad regional. Revisar cambios y esperar `CREATE_COMPLETE`.
4. Guardar parámetros y Outputs de ambos stacks sin guardar valores secretos. Los exports usan el nombre de cada stack; los futuros stacks podrán importarlos en la misma cuenta y región.

No necesitas instalar programas ni ejecutar comandos locales. Ningún archivo se ha desplegado como parte de esta preparación. La disponibilidad regional, cuotas y permisos se verifican en tu cuenta antes de crear; validación local no los comprueba.

## Qué crea cada plantilla

| Archivo | Recursos |
|---|---|
| `01-network-general.yaml` | VPC, ocho subredes privadas, ocho tablas y asociaciones, SG de RDS, administración y clientes DB; reglas TCP 5432 entre SG |
| `02-database.yaml` | DB subnet group, parameter group con TLS obligatorio, PostgreSQL Single-AZ cifrado, 20 GiB gp3, backups de 1 día configurables, contraseña maestra administrada por RDS en Secrets Manager |

Direccionamiento `10.X.0.0/16`, donde X es `VpcSecondOctet`:

| Par | AZ A | AZ B |
|---|---|---|
| General | `10.X.11.0/24` | `10.X.12.0/24` |
| Terceros | `10.X.13.0/24` | `10.X.14.0/24` |
| Onprem simulado | `10.X.15.0/24` | `10.X.16.0/24` |
| Database | `10.X.21.0/24` | `10.X.22.0/24` |

No hay IGW, NAT, rutas por defecto, EC2 admin ni endpoints de servicios. Todos los pares tienen ruta local de la VPC: las tablas y los nombres no aíslan tráfico por sí solos. RDS solo admite 5432 desde `AdminSecurityGroupId` o `DatabaseClientSecurityGroupId`; no desde un CIDR completo. El SG de clientes concede conectividad, no autorización SQL. Su asignación debe restringirse mediante IAM. Otros SG adjuntos pueden ampliar permisos porque sus reglas se suman.

## Primera comprobación: PostgreSQL

- Crear CloudShell **VPC environment** en `VPCId`, `GeneralSubnetAId` y **solo** `AdminSecurityGroupId`. No usar CloudShell estándar para la conexión privada. Revalidar presencia de `psql` y permisos para crear/eliminar ENI.
- Consultar el secreto indicado por `MasterSecretArn` desde la consola de Secrets Manager con una identidad autorizada. No pegarlo en archivos, comandos, capturas ni este chat. La cuenta maestra `lab_admin` es para administración; después crear usuarios de aplicación con privilegios SQL específicos.
- Con `psql` disponible, conectar al Output `DatabaseEndpoint`, puerto 5432, base `lab`, usuario `lab_admin`, TLS y contraseña solicitada interactivamente. Preferir `sslmode=verify-full` con CA RDS vigente disponible en el entorno. Sin Internet no se puede descargar la CA ni instalar paquetes desde ese entorno: preparar esos archivos mediante un método permitido antes de probar. `rds.force_ssl=1` exige cifrado, pero no sustituye validación del certificado por el cliente.
- Comprobar sesión y TLS con `SELECT current_database(), current_user;` y `SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid();`. Una conexión con `sslmode=disable` debe rechazarse.
- Desde otro entorno VPC con un SG de prueba que tenga salida TCP 5432 pero **sin** ninguno de los dos SG autorizados, conectar con `connect_timeout=5`: debe fallar por timeout. RDS no debe tener acceso público.

La base nace vacía. No se ejecuta automáticamente `aws/sql/init.sql`: revisar esquema y permisos según el flujo actual antes de aplicarlo. Tampoco existe aún outbox ni publisher.

## Continuación manual y automatización posterior

Flujo objetivo: API → micro-aceptación → PostgreSQL/outbox → Lambda publisher → SQS → EventBridge Pipe → Step Functions → microservicios. Los pods no necesitan iniciar Step Functions por este flujo.

Ahora puedes practicar manualmente. Por cada etapa, registrar en una plantilla nueva nombres, parámetros, roles, políticas, endpoints, configuración y validación obtenidos. Orden propuesto de archivos, **todavía no creados**:

| Archivo futuro | Alcance y decisiones pendientes |
|---|---|
| `03-network-endpoints.yaml` | Endpoints para servicios realmente usados y políticas específicas; sin copiar permisos ECR a otros servicios |
| `04-network-eks.yaml` | SG del clúster/nodos/pods, reglas y etiquetas para balanceador interno, usando las subredes base |
| `05-eks.yaml` | Clúster privado, acceso administrativo, tres Managed Node Groups, addons, ECR e identidades IAM |
| `06-messaging.yaml` | SQS/DLQ, workflow real, Pipe y permisos; crear Pipe detenido hasta tener consumidores/targets listos |
| `07-lambdas.yaml` | Publisher con contrato outbox, idempotencia y reintentos; authorizer con contrato de autenticación decidido |
| `08-api.yaml` | Tipo API, integración privada/VPC Link, balanceador interno y vínculo con authorizer |

La numeración organiza archivos; las dependencias reales determinan orden de despliegue. Primero hacen falta targets funcionales para habilitar el flujo completo. Para EKS sin NAT se decidirán endpoints de ECR API/DKR, S3, EC2 y EKS Auth según addons/Pod Identity; logs, SQS, Secrets Manager, STS u otros según clientes reales. El endpoint EKS de AWS no reemplaza el endpoint privado Kubernetes. Para publisher privado normalmente se necesitará acceso privado a SQS y a Secrets Manager, más sus permisos IAM. No crear `states` solo porque existe una máquina de estados.

Namespaces, NetworkPolicy, instalación/configuración de Istio, despliegues, imágenes y migraciones SQL requieren manifiestos/Helm y pasos de bootstrap versionados además de CloudFormation. CloudFormation por sí solo no captura estos cambios hechos con kubectl/Helm ni código de Lambda. El resultado final reproducible deberá incluir esos artefactos y sus instrucciones de arranque.

No crear luego un stack encima de recursos manuales esperando que los adopte: CloudFormation no los descubre automáticamente. Elegir importación de recursos cuando esté soportada, o recreación controlada; no borrar datos sin preservar lo necesario. No editar manualmente recursos propiedad de los dos stacks base; ampliar reglas desde stacks posteriores con recursos SG ingress/egress independientes evita perderlas al recrear la base.

## Bajar y volver a levantar

1. Guardar configuración/artefactos y verificar qué datos conservar. Eliminar primero aplicaciones, servicios/Ingress de Kubernetes y balanceadores mientras sus controladores siguen funcionando. Retirar después recursos manuales dependientes, ENI de CloudShell/Lambda/VPC Link, endpoints, nodos y clúster según sus dependencias.
2. Eliminar stacks posteriores antes de `micro-test-database`. RDS tiene `DeletionPolicy: Snapshot` y `UpdateReplacePolicy: Snapshot`: CloudFormation crea snapshot final al eliminar/reemplazar. Esperar y comprobar que el snapshot existe. No se habilita deletion protection para permitir este ciclo de laboratorio.
3. Eliminar `micro-test-network` al final. Imports activos o ENI/SG dependientes impedirán eliminarlo; revisar dependencias, no forzar borrados indiscriminados.
4. Recrear 01 y 02 con los parámetros guardados crea infraestructura equivalente y **una base vacía**. Recuperar datos requiere restauración explícita desde snapshot y adaptar/importar la instancia resultante; esta plantilla no implementa restauración. Outputs DNS/IDs/ARN cambiarán: actualizar consumidores y credenciales.

Eliminar stacks no significa costo cero: snapshots retenidos siguen facturándose; comprobar también recursos manuales y secretos restantes. RDS, almacenamiento/backups y Secrets Manager generan cargos. Más adelante se suman EKS, nodos, endpoints Interface y balanceadores. No se presume cobertura por créditos ni Free Tier.

## Validación y fuentes

`python3 cloudformation/validate.py` ejecuta comprobaciones estáticas de referencias, dependencias, CIDR, exports/imports y controles de red/RDS; usa PyYAML ya empleado por el repositorio. No requiere ejecutarlo en tu equipo para subir los YAML por consola. No sustituye validación/despliegue de AWS.

Fuentes oficiales consultadas el 2026-10-31:

- [CloudFormation: Subnet y AvailabilityZoneId](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ec2-subnet.html).
- [RDS: secreto administrado y snapshot al borrar](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/quickref-rds.html).
- [CloudShell en VPC y restricciones de conectividad](https://docs.aws.amazon.com/cloudshell/latest/userguide/using-cshell-in-vpc.html).
- [Restaurar una instancia desde snapshot](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html).
