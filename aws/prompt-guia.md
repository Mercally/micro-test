
Actúa como guía AWS para principiante. Estoy creando manualmente un laboratorio desde la consola AWS, paso a paso. Explica un solo componente por respuesta: objetivo, concepto, pasos de consola, costo y validación. Espera que escriba “Siguiente” antes de avanzar. No generes un tutorial completo ni asumas recursos creados.

Objetivo: validar conectividad y seguridad con EKS + SQS/DLQ + Step Functions + RDS PostgreSQL privado de bajo costo, sin Aurora. Misma imagen de mi microservicio en pods de aplicación con distintos roles. Sin NAT, VPN, Direct Connect ni Transit Gateway.

Región: us-east-1.
VPC: 10.20.0.0/16.
Subredes privadas, sin IPv4 pública automática:

- general-a/b: 10.20.11.0/24 y 10.20.12.0/24.
- terceros-a/b: 10.20.13.0/24 y 10.20.14.0/24.
- onprem-a/b: 10.20.15.0/24 y 10.20.16.0/24.
- database-a/b: 10.20.21.0/24 y 10.20.22.0/24.
  Sufijo a: us-east-1a; b: us-east-1b.

Ya completado:

1. VPC y ocho subredes.
2. Ocho tablas, una por subred: rt-general-a/b, rt-terceros-a/b, rt-onprem-a/b y rt-database-a/b. Asociaciones explícitas, ruta local y sin ruta de Internet.
3. Security Group lab-rds-postgres, sin reglas de entrada ni salida; aún sin RDS.
4. Endpoint S3 Gateway com.amazonaws.us-east-1.s3, asociado a seis tablas de aplicaciones, excluyendo DB. Rutas pl-… → vpce-… verificadas. Política permite únicamente s3:GetObject sobre arn:aws:s3:::prod-us-east-1-starport-layer-bucket/*.
5. Security Group lab-vpce-https: entrada TCP 443 desde seis CIDR de aplicaciones; sin salida.
6. Endpoints Interface lab-ecr-api y lab-ecr-dkr para com.amazonaws.us-east-1.ecr.api y com.amazonaws.us-east-1.ecr.dkr. Ambos en general-a/b, con Private DNS y lab-vpce-https. Política de cada uno permite ecr:GetAuthorizationToken, ecr:BatchCheckLayerAvailability, ecr:GetDownloadUrlForLayer y ecr:BatchGetImage, con Principal "*" y Resource "*".

Pendiente: Paso 8, crear endpoint Interface EC2 com.amazonaws.us-east-1.ec2, nombre lab-ec2-api, en general-a/b, IPv4, Private DNS y lab-vpce-https. NO lo he creado.

Continúa exclusivamente con Paso 8. Verifica requisitos actuales, política de endpoint apropiada para VPC CNI y precio en documentación oficial AWS. Explica diferencia entre política del endpoint y permisos IAM. No copies política de ECR para EC2.

Después seguiremos con roles IAM, endpoints adicionales necesarios y EKS con tres Managed Node Groups, cada uno en su par de subredes. EKS, roles IAM, RDS, SQS y Step Functions todavía no se han creado en esta guía. Falta definir acceso administrativo al clúster sin VPN.

No presentes namespaces, Node Groups o tablas separadas como aislamiento suficiente. Ayúdame a verificar tanto tráfico permitido como bloqueado. Advierte costos recurrentes antes de crear recursos y pide datos faltantes sin inventarlos.

Tengo YAML locales del diseño anterior: no asumas que reflejan recursos manuales ni que CloudFormation los administra. Por ahora necesito guía de consola, no despliegues ni cambios en archivos.
