# Evidencia 2026-11-05 — laptop

**Ruta activa y probada: API autenticada → NLB → Istio → aceptación/outbox;
publisher → SQS → Pipe → Step Functions HTTP → NLB → Istio → micro → PDF S3.**

| Prueba | Resultado | Alcance |
|---|---|---|
| NLB interno TCP, listener observado 4512 | PASS | Mismo NLB; targets NodePort Docker 31080 |
| HTTP Task de salud | PASS | SUCCEEDED, HTTP 200/UP; sin Lambda |
| Tres rutas API de negocio | PASS | Aceptar, consultar y retry apuntan a NLB; CUSTOM authorizer conservado |
| Compra completa periódica | PASS | PDF descargado, checksum y versión S3 |
| Historial compra normal | PASS | 7 HTTP Tasks ejecutados, 0 eventos Lambda; 8 tareas definidas incluyendo RecordFailure |
| JWT ausente, inválido, expirado | PASS | Rechazo por API autenticada |
| Idempotencia por referencia | PASS | Duplicado y conflicto de contenido comprobados |
| Error transitorio | PASS | Dos HTTP 503, retry y recuperación |
| Error permanente y retry de compra | PASS | Fallo registrado y nueva ejecución completada |
| Caída publisher y entrega duplicada | PASS | Recuperación sin duplicar resultado |
| Evento inválido | PASS | Rechazado; pruebas adicionales de UUID, generación y correlación |
| Pipe | RUNNING | Lote 1, ventana 0, FIRE_AND_FORGET |
| Aspire | RUNNING | Observador adaptado a HTTP Tasks, recurso http-nlb; telemetría reconstruida |
| HPA CPU + RAM | PENDIENTE WSL | Configuración CPU 60%, memoria media inicial 512Mi; no prueba de escalado ejecutada |
| Karpenter, workers reales y consolidación | PENDIENTE WSL | Usuario confirmó aplicar ZIP, no ejecutar instalación/node-test |
| VPC Lattice | NO SOPORTADO en inspección anterior | No bloquea ruta Docker; conectividad privada AWS sin validar |
| IAM enforcement / TLS de entrada | NO VALIDADOS | Configuración local no certifica aislamiento AWS |
| Persistencia | DESHABILITADA | No se reinició emulador |

## Informes

- [Aceptación consolidada](../reports/laptop/20261105T172511Z-5aefa3/nlb-http-acceptance.json).
- [Pruebas de negocio completas](../reports/laptop/20261105T171949Z-ead708/test.json).
- [Cambio de transporte](../reports/laptop/20261105T171842Z-eef5b2/http-cutover.json),
  con `before.json` y `after.json` para revisión de plantillas/parámetros.
- [Salud por HTTP Task/NLB](../reports/laptop/20261105T170839Z-66f028/probe.json).
- [Listener observado](../reports/laptop/20261105T170634Z-9f6823/nlb-network-inventory.json).

Compra comprobada: `beca95b4-52aa-4640-932f-446930fc205a`.
Ejecución: `arn:aws:states:us-east-1:000000000000:execution:micro-test-local-purchases:b0c1bce2-0839-43c7-a2b7-93be0254e6a6`.
PDF SHA-256: `ef82b10b584b681707005bd63309bcd2d2ac71189a192fcea1b0b3ade5f2d44c`.

## Corrección del diagnóstico anterior

Reportes `20261105T063400Z-9f6cc7` y `20261105T144800Z-c7d218` conservan errores
404 de pruebas contra puerto 4566. Era puerto incorrecto para listener TCP NLB.
Usar puerto observado 4512 resolvió ruta sin agregar otro componente.
No se eliminan esos fallos históricos ni se presentan como pruebas aprobadas.

Reportes están en Mac. ZIP transporta fuentes/documentación, no credenciales,
recibos de despliegue, tokens ni estado Docker/SQL. No se usó AWS real.
Ver [operación](NLB_HTTP.md) y [límites de conectividad](NETWORK_OPTIONS.md).
