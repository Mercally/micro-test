# Step Functions → NLB: decisión y límites

Verificado 2026-11-05, laptop LocalStack Pro `2026.10.4:a39e30ef`.
**Decisión aplicada: conservar NLB interno y comunicar por red Docker.**
Sin Lambda de transporte en Step Functions, API Gateway intermedio, ALB ni proxy.

## Causa del 404 y ruta que funciona

Las pruebas anteriores usaron `:4566`, entrada de APIs LocalStack/dispatch HTTP.
`DescribeListeners` devolvió **TCP `4512`** para el NLB existente. Con ese puerto:
HTTP Task → NLB → NodePort `31080` → Istio → micro devuelve HTTP 200/UP.
Después se probó compra completa con PDF, idempotencia y recuperación.
Los 404 históricos no demostraban ausencia de soporte NLB: apuntaban al puerto
incorrecto. LocalStack anuncia emulación de listeners NLB TCP desde
[versión 4.12](https://github.com/localstack/localstack/releases/tag/v4.12.0).

CFN mantiene listener TCP solicitado en puerto 80. Emulador asigna puerto de
servicio externo; scripts descubren valor efectivo, no fijan 4512 como constante.
NLB conserva `Scheme: internal`, mismo ARN y target group. Mac no alcanza 4512,
pero runtime HTTP dentro de LocalStack sí. No hace falta publicar puerto del host
ni agregar NAT. No hubo reinicio ni cambio global de Docker.

API Gateway de aceptación usa ese mismo hostname/puerto NLB como HTTP_PROXY,
con JWT/CUSTOM authorizer conservado. Step Functions invoca directamente NLB
mediante `arn:aws:states:::http:invoke`. Reutiliza Connection existente de prueba;
Connection no es otro proxy de aplicación ni EventBridge Event Bus.

## Diferencias frente a AWS

Step Functions es servicio gestionado, no una instancia dentro de subred EKS.
Endpoint VPC `states` permite **clientes en VPC → API de Step Functions**;
no habilita por sí mismo HTTP Task → NLB interno.
[AWS: VPC endpoints](https://docs.aws.amazon.com/step-functions/latest/dg/vpc-endpoints.html).

Para HTTP Task privado en AWS se configura conectividad privada de EventBridge
Connection. AWS exige HTTPS con dominio público y certificado públicamente
confiable; ese salto no soporta mTLS cliente. mTLS entre gateway Istio y micro
es otra conexión y puede mantenerse.
[AWS HTTP Task](https://docs.aws.amazon.com/step-functions/latest/dg/call-https-apis.html).

Este laboratorio usa **HTTP sobre red Docker**, con Connection de credenciales
ficticias; no demuestra conectividad Lattice/PrivateLink ni aislamiento VPC real.
VPC Lattice respondió servicio no soportado en inspección anterior. Tampoco se
simula VPC Link real en aceptación: integración local llama URI NLB alcanzable.
No se ha validado enforcement IAM ni seguridad productiva de endpoints internos.
No debe copiarse configuración HTTP/auth ficticia a AWS real.

NAT Gateway AWS permite salida, no publica NLB interno para recibir conexiones.
No se necesitó NAT ni publicación a Internet para resolver este caso local.

## Prueba ejecutada

[Informe de aceptación](../reports/laptop/20261105T172511Z-5aefa3/nlb-http-acceptance.json):
7 HTTP Tasks ejecutados en compra normal, cero eventos Lambda dentro del workflow;
9 comprobaciones de negocio PASS, PDF descargado y checksum validado.
Esto prueba ruta funcional local. No mide precio, latencia ni disponibilidad AWS.
Instrucciones de sincronización y práctica: [NLB_HTTP.md](NLB_HTTP.md).
