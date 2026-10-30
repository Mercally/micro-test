# Decisiones pendientes del laboratorio

Red privada, EKS con tres NG, SQS/DLQ, Step Functions y RDS PostgreSQL ya están representados en `01-network.yaml`, `02-eks.yaml` y `03-services.yaml`. Esta lista no propone stacks de ingreso ni conectividad externa.

## Entradas reales necesarias

- `S3PrefixListId` de `com.amazonaws.us-east-1.s3`.
- Principal IAM administrador persistente y host privado accesible por SSM, con perfil, AMI/herramientas y permisos definidos.
- Versiones compatibles de Kubernetes, release AL2023 y cuatro addons, verificadas en catálogos de `us-east-1`. El CNI debe soportar NetworkPolicy strict y Pod Identity.
- Versión PostgreSQL en soporte estándar y familia correspondiente, compatible con `db.t4g.micro` y 20 GiB gp3.
- Digest de la imagen real del micro en ECR privado, plataforma `linux/amd64`; mismo digest para los tres roles.
- DNS RDS, nombres de buckets y tres contraseñas de aplicación; CA regional vigente. Los JSON de ejemplo no contienen secretos ni IDs inventados.

## Reparto funcional

Requisito confirmado: únicamente el micro existente, replicado en distintos NG y con un rol por pod. Esto se implementa con tres Deployments, tres ServiceAccounts aislados por namespace y tres asociaciones Pod Identity. Todas las réplicas de un Deployment comparten su rol.

Asignación inicial de laboratorio: general puede iniciar dispatcher, terceros puede consumir cola, onprem solo tiene permisos S3 propios. La imagen actual sigue siendo HTTP/JDBC/S3: no incluye clientes SQS/Step Functions ni lógica que interprete una variable de rol. No se ha inventado un worker. Decidir qué operaciones realmente ejecutará cada rol antes de ampliar la misma imagen.

El dispatcher encola entrada JSON y confirma aceptación. No ejecuta el workflow completo de `workflow.http`, no espera resultado y no conecta automáticamente con DNS `*.svc.cluster.local`. Definir contrato de mensaje, iniciador, idempotencia, borrado/visibilidad, fallos y eventual callback antes de prometer procesamiento extremo a extremo. DLQ no es un Catch global de Step Functions.

Datos separados por defecto: tres bases lógicas y buckets independientes permiten comprobar aislamiento usando el mismo micro. Si los roles deben colaborar sobre una misma venta, acordar contrato de intercambio y privilegios SQL/S3 antes de compartir datos. El código actual no limita sus endpoints HTTP por rol.

## Operación y costo

- Confirmar presupuesto y horas encendido. EKS, tres EC2, RDS, diez endpoints Interface en dos AZ (20 ENI facturables), almacenamiento y posible tráfico entre AZ tienen cargos; endpoints pueden dominar el costo. No se afirma Free Tier ni precio mensual.
- Decidir disponibilidad: un nodo por NG y RDS Single-AZ son deliberados para laboratorio. Subir réplicas/nodos y Multi-AZ cambia costo y requisitos.
- Revisar retención SQS 4 días / DLQ 14 días, visibilidad 120 s, cinco recepciones, retries de envío y backups RDS de un día frente a duración y recuperación necesarias.
- Definir permisos de redrive/diagnóstico sin conceder administración a pods. Rotar credenciales de aplicación manualmente hasta definir mecanismo; el secreto maestro RDS no es el Secret Kubernetes.
- Definir limpieza explícita: RDS deja snapshot; buckets/ECR se retienen. No automatizar eliminación de datos ni migrar recursos previos con estas plantillas sin revisión.

No quedan pendientes VPN, Direct Connect, Transit Gateway ni Aurora: están fuera de alcance.
