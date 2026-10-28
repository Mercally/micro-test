# Stacks futuros: requisitos conocidos y conexión privada

No se crean `05-messaging.yaml` ni `06-database.yaml` en esta entrega. El alcance implementado es 01–04. Se solicitaron servicios y requisitos antes de diseñarlos; tus respuestas establecen **SQS con DLQ, Step Functions, PostgreSQL económico para demo y Aurora PostgreSQL con cómputo en dos AZ para producción**. Faltan decisiones operativas indicadas abajo; no se reemplazan por valores inventados.

## 05: SQS + DLQ + Step Functions

Como propuesta inicial para una demo sin requisito de orden estricto, evaluar SQS Standard. Entrega al menos una vez: los consumidores y las tareas deben ser idempotentes. FIFO se justifica si necesitas orden por venta o deduplicación propia de esa cola. La DLQ no elimina la necesidad de reintentos ni define por sí misma el tratamiento de errores de Step Functions. [Semántica SQS](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/standard-queues-at-least-once-delivery.html).

`../workflow.http` describe este orden, con respuestas que deben mantenerse en el estado del workflow:

1. `POST /api/v1/endpoint0/sales`: crear venta, guardar `saleId`.
2. `POST /api/v1/endpoint1/documents`: crear documento, guardar `documentId`.
3. `PATCH /api/v1/endpoint0/sales/status`: establecer `PROCESSING`.
4. `POST /api/v1/endpoint2/policies`: crear póliza, guardar `policyId`.
5. `POST /api/v1/endpoint3/documents/upload`: subir documento a S3.
6. `POST /api/v1/endpoint4/policies/complete`: completar póliza.
7. `PATCH /api/v1/endpoint0/sales/status`: establecer `COMPLETED`.
8. `POST /api/v1/endpoint5/documents/release`: devolver `downloadUrl`.

La petición final `PATCH ... status=FAILED` del archivo HTTP es una operación manual de prueba. En Step Functions debe ubicarse en una ruta `Catch`, no ejecutarse después de completar correctamente. Confirmar además si `COMPLETED` debe ocurrir antes o después de liberar el documento: el archivo actual lo marca antes. Reintentar una venta o póliza puede duplicarlas si no existe clave de idempotencia.

Por definir antes de generar plantilla: qué evento inicia la ejecución; contenido del mensaje; quién consume SQS y confirma/borra el mensaje; Standard/Express según duración y volumen; cómo Step Functions invoca el backend; timeout y backoff por tarea; retención principal/DLQ; `maxReceiveCount`; reglas de redrive; volumen y presupuesto de la demo. Una cola no dispara Step Functions por sí sola: se requiere integración de consumo, por ejemplo un adaptador o EventBridge Pipes, a seleccionar explícitamente.

SQS y Step Functions son servicios regionales fuera de la VPC. Si los pods llaman sus APIs sin NAT, agregar endpoints `sqs` y `states`, políticas y Pod Identity. Si el flujo usa callbacks/actividades, verificar también el endpoint específico y permisos de ese patrón. La ejecución administrada de Step Functions no se coloca automáticamente en una subred. El VPC Link de API Gateway no es una conexión SQL ni una conexión genérica que cualquier servicio AWS pueda reutilizar. Elegir y verificar una integración soportada para invocar el backend privado; no asumir acceso directo desde una tarea HTTP al Service de Kubernetes.

El simulador real también necesita bucket de documentos, permisos y configuración S3 de AWS. El laboratorio solo permite descargar capas ECR mediante S3; no se debe presentar ese endpoint como permiso suficiente para documentos de negocio. nginx únicamente valida el camino de red.

## 06: PostgreSQL para demo; Aurora PostgreSQL para producción

Para demostrar conectividad con una base administrada, el candidato a evaluar es **RDS PostgreSQL Single-AZ**, con clase pequeña compatible y almacenamiento mínimo permitido. No equivale a disponibilidad de Aurora en dos AZ. No se fija todavía clase, versión, almacenamiento, backups ni retención: “lo más económico” depende de horas encendida, créditos, requisitos de datos y operación. PostgreSQL autogestionado puede reducir algunos cargos, pero agrega mantenimiento y no reproduce el servicio administrado de producción.

La conexión prevista, tanto para RDS PostgreSQL privado como Aurora PostgreSQL privado, es:

```text
Pod EKS (IP dinámica de 10.20.16.0/20 o 10.20.32.0/20)
  → DNS del endpoint de la base (no IP fija)
  → ruta 10.20.0.0/16 local
  → security group de base, TCP 5432 desde SG autorizado de nodos/pods
  → ENI privada del motor en subred de datos
```

El futuro stack importará `VpcId`, `DataSubnetAId`, `DataSubnetBId` desde red y el SG de EKS —o un SG exclusivo de pods si se adopta después—. Creará DB subnet group con **ambas** subredes de datos y SG propio. `PubliclyAccessible: false`. No abrir 5432 a Internet ni a toda la VPC. Configurar cliente PostgreSQL con TLS y validación del certificado (`sslmode=verify-full`, CA de RDS), credenciales administradas y pool de conexiones. No guardar contraseña en parámetros sin protección ni en Git. [Aurora en VPC](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_VPC.WorkingWithRDSInstanceinaVPC.html).

**No hacen falta NAT, IGW, VPC Link ni endpoint Interface `rds` para el tráfico SQL.** La ENI de la base ya tiene direccionamiento privado en la VPC. El endpoint `rds` es para API de administración del servicio, no para conectar el driver JDBC. Si la aplicación obtiene credenciales de Secrets Manager, ese acceso sí requiere conectividad privada adicional e IAM. Si la base estuviera en otra VPC, serían necesarios conectividad y resolución DNS adicionales; no es el diseño planteado aquí.

Producción: DB subnet group en dos AZ y, para failover con cómputo listo, escritor en una AZ y al menos una réplica Aurora en la otra. Usar endpoint writer para escritura y reader cuando corresponda; no fijar IP ni el nombre de una instancia como sustituto del endpoint de clúster. Aurora replica almacenamiento en tres AZ regionales; “dos AZ” aquí describe ubicación del cómputo y subredes elegidas, no cambia esa arquitectura de almacenamiento. [Disponibilidad de Aurora](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Concepts.AuroraHighAvailability.html).

Pendientes para `06`: confirmar servicio de demo, versión PostgreSQL, horas/mes y límite de gasto, tamaño inicial, retención de backups, pérdida de datos tolerable, restauración requerida y tratamiento al borrar el stack. Para producción, definir capacidad provisionada/Serverless, réplica de failover, RPO/RTO y gestión de secretos. La red entregada ya reserva ambos CIDR de datos y exporta sus IDs; no se crea una base ficticia en el diagrama.
