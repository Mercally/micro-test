# Prompt para completar arquitectura local y practicar

Copia el bloque siguiente en una nueva sesión. El alcance incluye implementación;
no se limita a generar más documentación o fixtures.

```text
Trabaja en este repositorio y completa una versión funcional, pequeña y reproducible
del laboratorio local para practicar el flujo real de ventas de extremo a extremo.
Implementa los cambios y ejecuta las pruebas que permitan los accesos disponibles.

Contexto y destino
- Perfil AWS CLI: localstack-laptop; región us-east-1; credenciales ficticias test/test.
- LocalStack corre en una laptop Windows + WSL2 + Docker Desktop x86_64.
- Desde Mac: http://100.100.154.68:4566 por Tailscale.
- Desde WSL de esa laptop: http://127.0.0.1:4566.
- No usar el host anterior 100.95.224.37 para desplegar, probar o limpiar.
- Revalidar versión, licencia, recursos existentes, persistencia y enforcement IAM.
  La última lectura del host nuevo indicó 2026.10.4 Pro, licencia activa y
  persistencia desactivada; esos datos pueden haber cambiado.
- El usuario despliega inicialmente con localstack/LAPTOP.md. Inspecciona el
  resultado existente antes de crear o modificar recursos; no asumas un host vacío.

Lee primero AGENTS.md, LOCALSTACK_LAB.md, localstack/LAPTOP.md,
localstack/README.md, localstack/VALIDATION.md, cloudformation/README.md y la
configuración/código real del micro. Usa CodeGraph para consultas estructurales;
si falta contexto indexado en localstack/, lee los archivos concretos.
Los reportes antiguos corresponden a otra PC: no son resultados aprobados en laptop.

Arquitectura que debe quedar funcionando
API Gateway + authorizer local
  → micro de aceptación
  → PostgreSQL: venta + outbox en una misma transacción
  → Lambda publisher
  → SQS + DLQ
  → EventBridge Pipe
  → Step Functions de negocio
  → pasos HTTP del micro/microservicios
  → PDF en S3 + venta completada o error consultable.

Alcance autorizado
- Puedes modificar src/, pom.xml y tests solo cuando sea necesario para este flujo
  local: contratos, transacciones, outbox, idempotencia, estado y observabilidad.
  La restricción anterior de no tocar aplicación queda levantada para este alcance.
- Infraestructura local, Lambdas, ASL, scripts, configuración y fixtures nuevos
  quedan bajo localstack/.
- Mantén intactos aws/, cloudformation/, statemachine/ y el Compose raíz existentes.
  Si necesitas un arranque distinto, crea configuración independiente bajo localstack/.
- Conserva contratos HTTP existentes cuando sea posible; documenta cualquier cambio.
- No repartas pasos entre bases independientes. Empieza con una instancia del micro
  y la misma base; agrega separación solo con un contrato de datos comprobable.
- Evita nuevos frameworks, dashboards o servicios que no necesite la prueba real.

Implementación requerida
1. Aceptación y outbox transaccional
   - Aceptar una venta por API, devolver saleId/correlationId y estado aceptado.
   - Guardar venta y evento outbox atómicamente. Un rollback no deja venta huérfana
     ni evento sin venta. Definir eventId, tipo, versión y payload mínimo.
   - Hacer idempotente la repetición de la misma referencia/idempotency key.
   - Añadir forma de consultar estado final, error y documento sin mirar tablas a mano.

2. Lambda publisher real
   - Leer outbox desde PostgreSQL accesible desde su runtime; consultar secreto local.
   - Publicar a SQS y marcar publicado únicamente después de confirmación de envío.
   - Resolver concurrencia, locks o leases, reintentos y recuperación tras caída.
   - Asumir entrega al menos una vez. Demostrar el caso envío exitoso + fallo al marcar
     outbox; consumidores deben tolerar duplicados. No prometer exactly-once.
   - Proveer invocación manual y disparador periódico local realmente soportado.

3. Workflow de negocio
   - Conservar fixtures diagnósticos separados de colas/ASL/Lambdas de negocio.
   - Normalizar el evento real de Pipe: lote SQS, body JSON y versión de contrato.
   - Encadenar pasos actuales de documento, póliza, upload, finalización y liberación,
     conservando saleId/documentId/policyId y correlationId.
   - Usar integraciones de Step Functions verificadas en la versión instalada.
     Si hace falta Lambda adaptadora para HTTP, declararla como transporte local;
     no duplicar lógica de negocio ni presentarla como integración AWS nativa.
   - Implementar Retry/Catch acotados, estado COMPLETED/FAILED y recuperación definida.
   - Una ejecución Standard iniciada por FIRE_AND_FORGET puede fallar después de que
     SQS se haya confirmado. No atribuir esos errores automáticamente a la DLQ de SQS.
     Implementar y probar tratamiento explícito de errores del workflow.

4. API y autenticación local
   - Conectar API Gateway con el micro realmente alcanzable.
   - Implementar authorizer de laboratorio con contrato documentado y tokens de
     prueba generables localmente. Validar firma, expiración y claims pertinentes.
   - Probar token válido, ausente, mal firmado y expirado. No guardar claves privadas
     ni tokens sensibles en Git. La API diagnóstica no sustituye esta autenticación.

5. Runtime de laptop
   - Conseguir workers y pods Ready, tres Managed Node Groups con scheduling probado.
   - Reutilizar Dockerfile y variables reales; no inventar perfiles Spring.
   - Probar conexión PostgreSQL, push/pull ECR, S3 y URLs prefirmadas desde el cliente.
   - Instalar Istio compatible con la versión Kubernetes observada y demostrar mTLS.
   - Aplicar NetworkPolicy solo con controlador efectivo; probar allow y deny usando
     controles que descarten falsos negativos por DNS, pods caídos o falta de rutas.
   - Resolver conectividad por componente: localhost de pod/Lambda no es el host.
     4566 no da acceso automático a Docker, Kubernetes, registry ni PostgreSQL.
   - RDS es primera opción. Si el runtime no funciona, alternativa PostgreSQL local
     explícita y documentada; no migrar ni reemplazar una base con datos en silencio.

Protecciones obligatorias
- Solo endpoints locales autorizados y endpoint explícito en cada cliente AWS.
- Verificar identidad LocalStack antes de operar. Credenciales ficticias para
  bootstrap; identidades locales restringidas para tests IAM con enforcement activo.
- No llamadas a AWS real, ECR real, replicator, passthrough ni fallback externo.
- No heredar sesiones/roles reales ni imprimir/guardar LOCALSTACK_AUTH_TOKEN.
- Preservar datos por defecto. Borrados requieren opción explícita --delete-data.
- No limpiar Docker globalmente ni modificar firewall, Tailscale o Docker globales.
- Detectar recursos propios y cambios del usuario antes de editar o desplegar.
- Resolver cobertura mediante APIs y runtime, no solo CREATE_COMPLETE/ACTIVE.
- Revisar los workarounds existentes de Pipes y logs; no sustituirlos por comandos
  que esta instalación ya haya demostrado no soportar sin volver a verificar.
- Preparar persistencia durable antes de pruebas de reinicio. No reiniciar un
  emulador sin persistencia que contenga datos por conservar.

Pruebas de aceptación ejecutables
- Una petición autenticada produce una venta aceptada; termina en COMPLETED con
  PDF real en S3 y descarga válida, sin conducir manualmente cada paso HTTP.
- Dos peticiones con la misma clave no crean dos ventas/eventos lógicos.
- Rollback de aceptación conserva atomicidad venta/outbox.
- Reinicio/caída del publisher no pierde eventos; duplicados no duplican efectos.
- Fallo transitorio controlado se recupera; fallo permanente queda observable y
  recuperable según contrato. Probar DLQ por separado de errores del workflow.
- Mensajes inválidos/versiones desconocidas no se consideran ventas exitosas.
- Auth válida/ausente/inválida/expirada; IAM allow/deny con identidad restringida;
  mTLS y NetworkPolicy positivos/negativos.
- Reinicio del laboratorio conserva venta, outbox, objetos y estado recuperable.
  Comprobar datos originales antes de ejecutar operaciones que puedan regenerarlos.
- Los tests fallan ante errores reales; no convertir excepciones o falta de acceso
  en PASS. No usar solo mocks o fixtures para afirmar integración completa.

Entrega
- Código y artefactos implementados, cambios justificados y comandos exactos para
  desplegar, practicar, observar fallos, recuperar y limpiar conservando datos.
- Un guion de práctica: venta correcta, duplicado, fallo transitorio, fallo permanente,
  recuperación de outbox, consulta de logs/ejecución y verificación del PDF.
- Reportes separados por host/sesión, con versiones, comandos, PASS/FAIL/PENDIENTE
  y evidencia correlacionada. No reutilizar pruebas de la PC anterior.
- Si no tienes acceso WSL/Docker/Kubernetes, completa el código y pruebas disponibles,
  entrega comandos pendientes y explica el acceso faltante; no declares runtime aprobado.
```
