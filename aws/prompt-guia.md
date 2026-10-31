# Punto de reanudación: rehacer el laboratorio AWS con CloudFormation + consola

Fecha de actualización: 2026-10-31.

## Contexto y cambio de cuenta

- El laboratorio descrito abajo fue una práctica manual hecha en una cuenta AWS anterior identificada por el usuario como `metal_uli@hotmail.com`, en `us-east-1`.
- El usuario planea borrar los recursos de esa cuenta y empezar de nuevo en otra cuenta identificada como `josuemercally@gmail.com`. No asumir que ya borró nada, que la nueva cuenta está disponible o que algún recurso existe en ella. El asistente no debe borrar recursos.
- Región de la cuenta nueva: probablemente `us-east-2`, todavía no confirmado. No trasladar nombres de AZ ni asumir equivalencia de AZ entre cuentas; consultar disponibilidad/IDs de AZ y CIDR antes de diseñar.
- El usuario indica que tendrá US$200 de créditos por seis meses. Confirmar los términos reales del crédito y vigilar gastos: EKS, RDS, EC2, endpoints Interface, balanceadores y almacenamiento pueden generar cargos; apagar nodos no necesariamente elimina el cargo del control plane EKS ni los cargos de recursos persistentes.
- No solicitar ni escribir contraseñas, access keys, códigos MFA, tokens de inicio de sesión ni contenido de Secrets Manager. Las direcciones anteriores solo identifican las cuentas para el contexto del usuario.

## Preferencias de trabajo

- Guía para principiante, en español, un componente/paso por respuesta. Esperar “Siguiente” antes de avanzar. Explicar objetivo, concepto, pasos de consola, costos recurrentes y validación de tráfico permitido y bloqueado.
- El usuario quiere desplegar la infraestructura base con CloudFormation para no repetir manualmente la práctica inicial, pero completar manualmente desde la consola las Lambdas, API Gateway/authorizer, SQS, Step Functions y EKS.
- Importante: aunque CloudFormation abarque red base, el usuario quiere configurar manualmente la parte de networking específica de EKS. No desplegar ni editar plantillas/recursos hasta que el usuario lo solicite explícitamente.
- El usuario no puede instalar programas en su computadora. En la práctica anterior pudo abrir CloudShell VPC y comprobó `psql` 16.15 en CloudShell estándar; todavía no reportó resultado de conexión a RDS. CloudShell VPC es efímero. Revalidar soporte/permisos y región al empezar la nueva cuenta.
- Proporcionar fuentes oficiales AWS y fecha de consulta en respuestas de guía. No asumir recursos creados por YAML locales ni que CloudFormation los administra.

## Objetivo del laboratorio nuevo

Practicar conectividad, resiliencia y seguridad con EKS, PostgreSQL privado en RDS (no Aurora), SQS/DLQ, Lambda, EventBridge Pipes y Step Functions. Sin NAT Gateway, VPN, Direct Connect ni Transit Gateway, salvo que el usuario cambie el requisito.

- Una única imagen del microservicio, ejecutada en múltiples pods con identidades/roles distintos.
- Investigar namespaces, varios Managed Node Groups y Istio/service mesh como mecanismos del ejercicio, pero no presentarlos como aislamiento suficiente por sí mismos. Definir controles complementarios (IAM/Pod Identity, NetworkPolicy/CNI, SG for Pods cuando proceda, autorización de clúster, políticas de endpoint y controles de red).
- La arquitectura resumida de la parte asíncrona:
  1. Solicitud externa entra a micro-aceptación.
  2. Micro-aceptación guarda el estado inicial/outbox en PostgreSQL.
  3. Lambda publisher lee outbox y publica eventos en SQS (posiblemente con DLQ).
  4. EventBridge Pipes consume SQS e inicia Step Functions.
  5. Step Functions invoca los microservicios; estos no invocan la Step Function.
  6. La alternativa planteada para exponer micro-aceptación es API Gateway Regional con integración privada/VPC Link y balanceador interno. Aún hay que decidir tipo API, auth/authorizer, balanceador y detalles de integración.

## Alcance deseado de CloudFormation (infraestructura base)

Planear y revisar primero; todavía no construir/desplegar. El usuario quiere que un stack cubra los cimientos, incluyendo:

- VPC, CIDR a confirmar, subredes de aplicación/terceros/on-prem/database, tablas de rutas y asociaciones, sin rutas públicas/NAT por requisito.
- Security Groups para RDS, endpoints y host de administración con mínimo privilegio.
- VPC endpoints requeridos tras decidir servicios/endpoints exactos y sus endpoint policies. No copiar política de ECR para otros servicios; separar endpoint policies, IAM y controles de red.
- DB subnet group y RDS PostgreSQL privado, cifrado, backups, tamaño/AZ/retención y manejo de secreto a decidir según costos y requisitos.
- EC2 admin privado solo si sigue siendo necesario; el método probado/seleccionado para consola DB fue CloudShell VPC porque no puede instalar el Session Manager plugin local.
- Recursos de observabilidad mínimos que se acuerden.

Especificar parámetros y outputs útiles; usar tags y prevenir recursos públicos por defecto. Diseñar eliminación segura y retención de datos/backups. No incluir automáticamente componentes que el usuario quiere crear manualmente en la consola.

## Componentes a dejar para creación manual después del stack

1. Lambda authorizer ligero y API Gateway (confirmar responsabilidades y tipo API antes de concretar).
2. Lambda publisher de la outbox.
3. SQS y DLQ, más EventBridge Pipes.
4. Step Functions.
5. EKS y workloads; crear manualmente la configuración de red específica de EKS y tres Managed Node Groups en los pares de subredes que se definan.
6. API Gateway Regional/VPC Link/target interno en la forma acordada.

No asumir orden final entre los componentes anteriores hasta revisar dependencias y permisos. Roles IAM y Pod Identity deben respetar privilegio mínimo.

## Registro de práctica anterior (solo histórico; NO estado de la cuenta nueva)

La práctica fue hecha en `us-east-1`, VPC `10.20.0.0/16`, con ocho subredes privadas:

- general-a/b: `10.20.11.0/24`, `10.20.12.0/24`
- terceros-a/b: `10.20.13.0/24`, `10.20.14.0/24`
- onprem-a/b: `10.20.15.0/24`, `10.20.16.0/24`
- database-a/b: `10.20.21.0/24`, `10.20.22.0/24`

La práctica reportó tablas de ruta explícitas sin ruta de Internet; endpoints S3 Gateway, ECR API/DKR, EC2, EKS Auth, SQS, SSM y SSM Messages; varios SG; RDS PostgreSQL privado; DB subnet group; EC2 admin privada; CloudShell VPC con SG reutilizado `lab-admin-host`. También se reportó endpoint `states`, que no era necesario para la arquitectura establecida si ningún cliente dentro de la VPC llama a Step Functions. El usuario tuvo un problema de confianza TLS local y no pudo instalar el Session Manager plugin, razón por la que se prefirió CloudShell VPC. **Nada de esta lista confirma que continúe existiendo; el usuario planea borrar los recursos de la cuenta anterior.**

La dirección/endpoint de RDS anterior no se debe reutilizar en el laboratorio nuevo. La conexión `psql` anterior quedó pendiente de verificación.

## Próximo inicio de la siguiente sesión

1. Confirmar que no se pide ejecutar borrados ahora y que los recursos de la cuenta anterior ya fueron eliminados o si el usuario desea una guía de limpieza controlada. Antes de borrar datos, confirmar snapshots/backups que quiere conservar.
2. Confirmar acceso a la cuenta nueva, región (probablemente `us-east-2`), crédito real, presupuesto/alertas y restricciones de permisos.
3. Acordar CIDR, subredes y AZ IDs/pares de AZ disponibles; aclarar alcance exacto del stack base versus configuración manual de networking EKS.
4. Diseñar, revisar costos, y solo con autorización explícita crear/editar CloudFormation.
5. Seguir una acción por respuesta, esperar “Siguiente”.

No presentar namespaces, Node Groups, tablas de rutas ni subredes como controles de aislamiento suficientes. Validar caminos permitidos y bloqueados en cada fase; no inventar IDs, políticas, secretos, permisos ni estados de recursos.
