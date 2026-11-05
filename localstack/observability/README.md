# Visor de compras con Aspire

Dashboard standalone en Mac, conectado al LocalStack de laptop. No necesita
ExecutionArn: usa el `saleId` devuelto por `POST /purchases`.

## Abrir y practicar

1. Abre <http://127.0.0.1:18888/traces> **en Mac**.
2. Envía **Accept** desde `localstack/.state/business/purchase.http`.
3. Copia `saleId`. En **Traces**, cuando termine, búscalo en **Filter** y abre
   `Compra <saleId>`. Verás waterfall, pasos e intentos HTTP bajo recurso `http-nlb`. Trazas antiguas conservan `lambda-transport`.
4. Mientras procesa, abre **Structured logs** y usa **Add filter** con atributo
   `saleId`, condición **contains**, valor UUID completo. Los mensajes `Entró en Document`, `Salió de Document`,
   errores y `Workflow SUCCEEDED` permiten seguir avance. Selecciona todos los
   recursos para incluir publisher y transporte.
5. Prueba bloque **Transient** del `.http` con referencia nueva: dos intentos
   fallidos en Document, tercero correcto, compra COMPLETED. **Permanent** permite
   inspeccionar Upload fallido; **Retry** crea otra ejecución para la misma venta.

El observador consulta cada tres segundos, más tiempo de llamadas. Un paso muy
rápido puede aparecer ya terminado. Los spans se exportan al terminar cada paso;
la raíz `Compra` aparece al terminar workflow. Durante ejecución usa logs de
entrada/salida y `observe.py status` (`active[].currentStep`).

Mientras compra está ACCEPTED y espera publisher (programación cada minuto),
todavía puede no existir traza. Consulta **Poll** del `.http`; ausencia de traza
no significa compra rechazada. Requests rechazadas por autenticación no producen
workflow y tampoco aparecen en este visor.

## Arrancar o detener

Desde raíz del repositorio **en Mac**, con Aspire CLI 13.6.x instalado y en PATH:

```bash
export PATH="$HOME/.aspire/bin:$PATH"
localstack/.venv/bin/python -m pip install -r localstack/observability/requirements.txt
localstack/.venv/bin/python localstack/observability/observe.py start \
  --endpoint http://100.100.154.68:4566
localstack/.venv/bin/python localstack/observability/observe.py status
```

`start` devuelve antes de finalizar arranque. Espera `status: RUNNING` y
`supervisorAlive: true`; primer arranque puede descargar dashboard. Repetir start
con servicio vivo no lo duplica. Requiere puertos libres 18888, 4317 y 4318.

```bash
localstack/.venv/bin/python localstack/observability/observe.py stop
localstack/.venv/bin/python localstack/observability/observe.py status
```

Espera `supervisorAlive: false` antes de arrancar de nuevo. Detiene solamente
observador y dashboard. No modifica triggers, pods ni LocalStack. No arranca
automáticamente al reiniciar Mac. No hace falta ejecutar nada nuevo en WSL para
usar visor de Mac.

Para alojarlo en WSL, copia esta carpeta al checkout actualizado, instala Aspire
CLI 13.6.x y dependencias del lab/observador, usa los mismos comandos con
`--endpoint http://127.0.0.1:4566`. Esa variante no fue ejecutada en laptop.
Instalación oficial: <https://aspire.dev/dashboard/standalone/>.

## Qué representa

| Vista | Fuente real |
|---|---|
| Compra / Step Functions | Fechas y estado terminal de ejecución SFN |
| Normalize, Claim, Document, Policy, Upload, etc. | StateEntered / StateExited del historial |
| Intentos HTTP/NLB, fallos y reintentos | TaskScheduled/Started/Succeeded/Failed del historial; servicio `http-nlb` |
| Intentos Lambda históricos | LambdaFunctionStarted/Succeeded/Failed; servicio `lambda-transport` |
| SQS → inicio de Step Functions | SentTimestamp hasta inicio SFN; intervalo combinado de espera y despacho |
| Outbox publicado / transporte histórico | Logs JSON existentes de publisher y transport en CloudWatch; nuevas llamadas HTTP no generan logs del transport Lambda |

**Telemetría reconstruida**, marcada `telemetry.reconstructed=true`. TraceId
determinista por ExecutionArn; `saleId`, `eventId` y `correlationId` permiten
correlación. Múltiples ejecuciones de una venta conservan trazas separadas.
Logs del publisher se correlacionan por atributos; no se les inventa un padre
cuando evento tiene varias ejecuciones.

No representa spans HTTP/SQL nativos ni instrumentación interna del PDF/S3.
Upload exitoso muestra paso exitoso, no una medición independiente de S3.
Istio instalado gestiona tráfico; este visor no instala Kiali ni activa agente
Java OpenTelemetry. Para trazas distribuidas completas falta instrumentar API,
micro y Lambdas y propagar contexto por outbox/SQS. Documentación de dashboard:
<https://aspire.dev/dashboard/configuration/>.

## Retención y operación

- Recupera últimas 100 ejecuciones SFN al arrancar y sigue ejecuciones activas ya
  vistas. No está diseñado para tráfico que supere 100 ejecuciones entre polls.
- Importa logs de últimas 24 horas; luego consulta ventana superpuesta de cinco
  minutos. Logs más tardíos pueden requerir reiniciar observador para relectura.
- Datos Aspire y ledger de exportación persisten juntos bajo
  `localstack/.state/observability/`, ignorados por Git. Resume conserva trazas
  al reiniciar visor. Límites: 5.000 trazas y 50.000 logs.
- No uses **Remove data** si quieres conservar historial: ledger evita reenviar
  registros ya exportados. Para una sesión nueva, detén visor y mueve carpeta
  completa `.state/observability` a un respaldo antes de arrancar.
- UI escucha únicamente en loopback; API y OTLP requieren claves locales
  generadas en `dashboard.json`. No compartas ese archivo.
- Exporta campos permitidos; omite payloads de clientes, JWT, URLs firmadas y
  causas de excepción sin filtrar. No consulta AWS real.
- `DEGRADED` indica fallo de lectura/exportación: reintenta automáticamente.
  Revisa `observer.log`, `dashboard.log` y conectividad a laptop.
- Persistencia de visor **no demuestra persistencia de LocalStack**. Emulador
  conserva su advertencia de persistencia deshabilitada; no se reinició.

## Verificación

```bash
localstack/.venv/bin/python -m unittest discover \
  -s localstack/observability -p 'test_*.py' -v
```

Pruebas cubren jerarquía, estados activos, reintentos, errores permanentes,
redacción, rechazo de recursos ajenos y reintento de exportación fallida.
Evidencia de ejecución real se guarda en `localstack/reports/laptop/`.

Validación ejecutada: nueve tests aprobados; compra
`0350ece3-2b84-48e8-92ed-774ae32523a7` observada en Normalize y Document mientras
procesaba, final COMPLETED con PDF verificado. Aspire recibió 23 spans, dos
intentos fallidos y 31 logs correlacionados. Waterfall y filtro saleId comprobados
en navegador. Reinicio de visor conservó 13 trazas previas; compra nueva elevó
total a 14.
Reporte: [observability.json](../reports/laptop/20261105T033814Z-0cdb42/observability.json).
