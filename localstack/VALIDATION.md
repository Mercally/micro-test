# Validación: separar evidencia histórica de arranque nuevo

## Flujo existente en laptop — probado el 2026-11-05

[Evidencia curada](evidence/nlb-purchase-20261105.json): compra autenticada hasta PDF,
idempotencia, JWT ausente/inválido/expirado, dos HTTP 503 con recuperación, error
permanente/retry y duplicado tras caída del publisher. Compra normal: 7 tareas HTTP,
cero eventos Lambda dentro del workflow. Tres rutas API apuntan al mismo NLB.
Usuario confirmó implementación de paquete NLB en WSL.

Listener observado entonces: TCP 4512. Los 404 previos provenían de usar 4566,
gateway de APIs, como puerto NLB. Arranque nuevo descubre listener; no fija 4512.
Aspire observó compras históricas; visor usa historial SFN reconstruido, no spans
nativos de aceptación, SQL ni S3.

## Arranque consolidado desde cero

**Preparado; aceptación completa PENDIENTE en WSL limpio.** No se reinició ni
redeplegó laboratorio existente para validar esta reorganización.

| Comprobación | Estado |
|---|---|
| Contratos, descubrimiento/ownership, límites HPA y proyección de trazas | Pruebas offline; ejecutar `./localstack/lab verify` |
| Rechazo de emulador ajeno, etapas obligatorias, fallo Karpenter, no exposición de token | Cubierto por tests de bootstrap |
| Plantillas locales y ASL HTTP directo | Validación estática; no equivale a CFN CREATE_COMPLETE |
| Cleanup Docker, helpers y volúmenes en WSL | Intento bloqueado antes de modificar Docker; huella corregida, reintento WSL PENDIENTE |
| Primera instalación íntegra en WSL limpio | PENDIENTE |
| HPA con muestras reales de CPU/RAM en instalación nueva | PENDIENTE; obligatorio en `up` |
| Karpenter → NodeClaim → worker real → HTTP | PENDIENTE; obligatorio en `up` |
| Escalado/reducción HPA y consolidación de nodos | PENDIENTE, ejercicios separados |
| Persistencia nueva | Configurada con volumen y PERSISTENCE=1; recuperación tras reinicio PENDIENTE |
| Enforcement IAM / conectividad privada AWS / TLS de entrada | NO CERTIFICADOS por lab |
| CloudFormation AWS real | PENDIENTE de diseño final/generación; no desplegado |

`up` solo termina con PASS si todas sus etapas obligatorias pasan. Un controller
Ready, APIs EC2/EKS en estado available/active, aplicar ZIP o renderizar Helm no
cuentan como worker real funcional. Reporte registra etapa fallida y mantiene
triggers detenidos si el arranque queda incompleto.

Verificación ejecutada en Mac tras reorganización: **75 pruebas PASS, 3 omitidas**,
Compose válido y cfn-lint sin errores. Repetido en copia limpia sin `.state`
ni reportes/cachés iniciales. [Resumen](evidence/repository-validation-20261105.json). Warnings de plantillas base: AZ fijas,
contraseña ficticia local y versión PostgreSQL genérica `16`; no son plantillas AWS.
Definición HTTP contrastada por lectura contra workflow vigente: equivalente;
Pipe RUNNING y Scheduler ENABLED. Ningún despliegue/reinicio del lab existente.

Tras añadir cleanup: **93 pruebas PASS, 3 omitidas**, incluyendo 18 pruebas de
protecciones y recuperación de borrado parcial con Docker simulado. CLI de ayuda
y lint verificados. [Evidencia cleanup](evidence/cleanup-validation-20261105.json).
Salida WSL posterior confirmó pausa de triggers y rechazo de `purge` en primera
comprobación de huella, antes de parar/eliminar contenedores. Reproducido offline
al invertir orden de montajes; corregido con huella canónica y planes schema 2.
Tras corrección pasan 32 tests de `localstack/tests` (21 de cleanup). Causa exacta
en Docker y borrado completo requieren reintento WSL; no hay evidencia de éxito todavía.

Tests PostgreSQL requieren instancia/base aisladas; si faltan, se muestran como
omitidos. No usar DB de compras para habilitarlos: limpian tablas de su base de test.

## Evidencia que falta recoger

En motor Docker aislado o limpiado deliberadamente según [CLEANUP.md](CLEANUP.md), ejecutar `./localstack/lab up`; conservar `bootstrap.json`,
`node-test.json` y `purchase-test.json` generados en `localstack/reports/laptop/`.
Después ejecutar ejercicios CPU/RAM y consolidación de [scaling/README.md](scaling/README.md).
Para persistencia hace falta plan de pausa, snapshot de compra/PDF y reinicio
controlado **solo del emulador nuevo**, con comprobación SQL/S3/EKS posterior.
No realizar ese reinicio sobre lab anterior sin persistencia.

Reportes brutos quedan fuera de Git; solo evidencia curada y sin secretos se
versiona en `evidence/`. Fuentes anteriores, fixes por ZIP y reportes históricos
versionados siguen disponibles en Git, no son instrucciones vigentes.
