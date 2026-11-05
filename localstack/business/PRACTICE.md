# Practicar compras desde un .http

Usa scripts actualizados con EventBridge Scheduler. El laboratorio está en laptop;
Mac accede por `http://100.100.154.68:4566`, WSL por `http://127.0.0.1:4566`.
Arranque central: [../README.md](../README.md). Emulador nuevo crea solo Scheduler;
compatibilidad con regla antigua se conserva únicamente al operar lab anterior.

## Generar requests

Desde raíz del repositorio en WSL:

```bash
export LAB_ENDPOINT=http://127.0.0.1:4566
./localstack/lab status --endpoint "$LAB_ENDPOINT"
./localstack/lab resume --endpoint "$LAB_ENDPOINT"
./localstack/lab http --endpoint "$LAB_ENDPOINT"
```

En Mac usa `export LAB_ENDPOINT=http://100.100.154.68:4566` y mismos comandos.
Abre `localstack/.state/business/purchase.http` con extensión VS Code REST Client.
Archivo generado contiene tokens locales, queda fuera de Git y vence en una hora;
repite comando `http` para renovarlo. La plantilla bajo `business/requests/` contiene
marcadores y no se envía directamente.

## Compra y seguimiento

1. Ejecuta **Accept**, primer `POST /purchases`: debe devolver 202 con saleId/eventId.
2. Ejecuta **Poll**, `GET /purchases/{saleId}`, hasta COMPLETED. Puede comenzar en
   ACCEPTED mientras espera publisher periódico, luego pasar a PROCESSING.
3. Ejecuta **Download** después de consultar estado COMPLETED: descarga PDF de S3.
4. Repite POST original sin cambiar referencia/contenido: conserva saleId/eventId.
5. Envía bloque **Same reference, different payload**: debe responder 409.

Flujo observado: API Gateway/authorizer → NLB → Istio → micro → transacción PostgreSQL
venta+outbox → Lambda publisher periódico → SQS → Pipe → Step Functions HTTP → NLB → Istio →
pasos del micro → PDF S3. Consultar estado no ejecuta pasos ni regenera el PDF.

## Ejercicios de error

| Bloque del .http | Resultado esperado | Evidencia |
|---|---|---|
| Missing / Bad signature / Expired token | 401/403 | Respuesta de API Gateway |
| Transient | Recuperación y COMPLETED | Dos errores HTTP 503 en historial SFN y reintentos |
| Permanent | FAILED recuperable | Error de upload consultable y ejecución SFN FAILED |
| Retry después de FAILED | Nueva generación, COMPLETED | Nuevo eventId; conserva venta y pasos terminados |

Para repetir ejercicios usa una referencia nueva o regenera `.http`. Una referencia
ya completada sigue siendo idempotente aunque cambies el header de fallo.

## Caída del publisher después del envío

Pausa solo programación; conserva Pipe activo:

```bash
localstack/.venv/bin/python localstack/business/business.py pause-publisher --endpoint "$LAB_ENDPOINT"
```

Crea otra compra con referencia nueva desde `.http`. Copia su eventId:

```bash
localstack/.venv/bin/python localstack/business/business.py publish \
  --endpoint "$LAB_ENDPOINT" --event-id EVENT_ID --crash-after-send
```

Fallo LAB_CRASH_AFTER_SEND es intencional. SQS recibió mensaje; outbox queda sin
published_at. Espera al menos cinco segundos, vuelve a publicar y reanuda:

```bash
localstack/.venv/bin/python localstack/business/business.py publish \
  --endpoint "$LAB_ENDPOINT" --event-id EVENT_ID
localstack/.venv/bin/python localstack/business/business.py resume-publisher --endpoint "$LAB_ENDPOINT"
```

Dos ejecuciones pueden compartir evento; efectos de negocio conservan un documento
lógico. Entrega es al menos una vez. La suite automatizada comprueba este caso:

```bash
./localstack/lab test --endpoint "$LAB_ENDPOINT"
```

## Observar y detener

Visor gráfico disponible en Mac: [Aspire local](http://127.0.0.1:18888/traces).
Busca compra por `saleId`; abre waterfall de pasos y reintentos. Para progreso
activo usa Structured logs filtrados por atributo `saleId`.
[Arranque, ejercicios y alcance de telemetría](../observability/README.md).
Vista reconstruida desde historial SFN y logs, sin spans internos HTTP/SQL.

```bash
aws --profile localstack-laptop --endpoint-url "$LAB_ENDPOINT" --region us-east-1 \
  logs tail /aws/lambda/micro-test-local-publisher --since 10m
aws --profile localstack-laptop --endpoint-url "$LAB_ENDPOINT" --region us-east-1 \
  stepfunctions list-executions \
  --state-machine-arn arn:aws:states:us-east-1:000000000000:stateMachine:micro-test-local-purchases
./localstack/lab pause --endpoint "$LAB_ENDPOINT"
```

`pause` deshabilita Scheduler y detiene Pipe; ejecuciones iniciadas pueden terminar.
No borra ventas, outbox, colas ni PDFs. `resume` reanuda. Lab anterior tiene persistencia deshabilitada;
nuevo arranque configura volumen, pero recuperación debe probarse por separado.
Estado de pruebas: [VALIDATION.md](../VALIDATION.md).

Prueba de pausa real durante 75 segundos y recuperación automática después de reanudar:

```bash
localstack/.venv/bin/python localstack/business/business.py test-pause --endpoint "$LAB_ENDPOINT"
```

Ejecutar con flujo iniciado y sin otro proceso cambiando Scheduler. Crea venta de
práctica; restaura programación al terminar incluso ante error. No reinicia emulador.
