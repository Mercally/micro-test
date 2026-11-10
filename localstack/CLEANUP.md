# Detener, borrar y reconstruir el laboratorio

Entrada única: `./localstack/lab cleanup`. Ejecutar desde raíz del checkout actualizado
**dentro de WSL**, sobre `/var/run/docker.sock`. No usa AWS real ni Docker remoto.
Para instalaciones administradas por el lanzador, `./localstack/lab down --delete-data`
automatiza inventario, pausa, purge y eliminación de `.state`, conservando venv y
reportes. La licencia privada en `~/.config/micro-test/localstack-token` se conserva.
Este procedimiento detallado sirve para migrar instalaciones anteriores o revisar
helpers que no pudieron atribuirse automáticamente.
El 2026-11-10 se ejecutó purge real del lab roto: nueve contenedores, dos redes y
22 volúmenes seleccionados; los dos PostgreSQL de otros proyectos se conservaron.

**El laboratorio anterior tiene persistencia deshabilitada. Detener su emulador puede
perder compras, SQL, PDFs y configuración. `purge` borra definitivamente los datos del
emulador y volúmenes seleccionados. El plan JSON y los snapshots de pruebas no son un
respaldo recuperable. Exporta cualquier dato que quieras conservar antes de continuar.**

| Acción | Efecto |
|---|---|
| `./localstack/lab pause` | Detiene publisher y Pipe; mantiene pods, emulador y workflows en curso |
| `cleanup plan` | Lee inventario, guarda selección revisable; no detiene recursos |
| `cleanup stop` | Detiene contenedores seleccionados; conserva contenedores/volúmenes salvo helpers AutoRemove de Docker |
| `cleanup purge` | Detiene y elimina contenedores, redes y volúmenes seleccionados |
| `cleanup files` | Tras purge, elimina archivos generados de este checkout: `.state`, `.venv`, `reports`, cachés Python |

`stop`, `purge` y `files` son simulación salvo `--apply`. `purge` y `files` exigen
además `--delete-data`. `stop` exige ese reconocimiento cuando el plan no observa
persistencia habilitada. `PERSISTENCE=1` tampoco demuestra recuperación integral;
no uses `stop` como respaldo ni presupongas que EKS/RDS volverán solos.

## 1. Inventario y revisión, sin borrar

Evita ejecutar `up`, tests, nuevas requests o herramientas que creen recursos durante
cleanup. No ejecutes operaciones simultáneas desde otro checkout. El bloqueo del
lanzador protege `up`/`cleanup` dentro de este checkout, no todos los clientes Docker.

```bash
./localstack/lab cleanup plan \
  --emulator localstack-aws \
  --plan "$HOME/micro-test-cleanup.json"
```

Para instalaciones creadas por el lanzador nuevo, el nombre es
`--emulator micro-test-local-emulator`. El emulador debe seguir encendido para leer
propiedad de stacks/EC2/Lambda. Después de detenerlo, conserva y reutiliza el plan.
El JSON debe quedar **fuera del checkout**, con permisos 600; no incluye variables
Docker, contraseñas ni payloads de compras. Revisa:

- `containers`: emulador elegido, nodos correlacionados con EC2 propio, runtimes
  Lambda correlacionados con funciones de stacks y helpers seleccionados explícitamente.
- `volumes` y `networks`: recursos que eliminará `purge`; excluye recursos con
  usuarios ajenos y redes Docker incorporadas (`bridge`, `host`, `none`).
- `unselectedContainers`: puede contener PostgreSQL/RDS, workers Karpenter o helpers
  antiguos sin metadatos suficientes. **No significa que todos sean ajenos al lab.**
- `unselectedNetworks`, `unselectedVolumes`, `retained`, `bindMountsRetained`: recursos
  que conservará. Montajes de directorios del host nunca se borran automáticamente.
- `images`: etiquetas propias que eliminará únicamente con `purge --images`.

Si necesitas identificar helpers, consulta localmente `docker ps -a --no-trunc`,
`docker network ls --no-trunc`, `docker volume ls` y mounts/labels del ID concreto.
No compartas `docker inspect` completo: puede contener token LocalStack y claves.
No atribuyas un recurso al lab solamente por fecha, imagen PostgreSQL o nombre parecido.

Tras comprobar propiedad, regenera plan agregando IDs completos observados:

```bash
# Sustituye marcadores; incluye solo opciones necesarias.
./localstack/lab cleanup plan \
  --emulator localstack-aws \
  --include-container ID_COMPLETO_HELPER \
  --include-network ID_COMPLETO_RED \
  --include-volume NOMBRE_EXACTO_VOLUMEN \
  --plan "$HOME/micro-test-cleanup.json"
```

Opciones repetibles. Por ejemplo, `localstack-ec2-link-local` requiere revisar usuarios
antes de incluir su ID; no se borra por nombre. También pueden quedar volúmenes sin
contenedores. Recursos compartidos permanecen excluidos incluso si los incluyes.
Si no puedes establecer propiedad, conserva el recurso y revisa el inventario antes
de intentar liberar todo su espacio.

## 2. Pausar y generar plan definitivo

Cuando decidas descartar/reconstruir el lab, detén nuevas requests y ejecuta:

```bash
./localstack/lab pause
./localstack/lab status
```

Espera fin de workflows activos. Cleanup rechaza operaciones mientras Pipe/Scheduler
estén activos o queden ejecuciones RUNNING. Regenera plan del paso 1 después de pausar,
repitiendo selecciones explícitas necesarias. Así incluye últimos runtimes Lambda.
Si aparece otro contenedor antes de aplicar, operación se detiene para revisar de nuevo.

Planes actuales usan `schema: 2`: la huella compara montajes sin depender de su
orden, conservando todos sus campos. Si tienes un plan `schema: 1`, actualiza el
checkout y regenera el plan mientras el emulador siga encendido. No cambies el
número de schema a mano. La versión anterior podía informar `Container
identity/configuration changed` al recibir los mismos montajes en distinto orden.

Para detener sin eliminar volúmenes, revisa primero y luego aplica conscientemente:

```bash
./localstack/lab cleanup stop --plan "$HOME/micro-test-cleanup.json"
# ADVERTENCIA: lab antiguo sin persistencia puede perder datos al detenerse.
./localstack/lab cleanup stop --plan "$HOME/micro-test-cleanup.json" --apply --delete-data
```

Este paso es opcional: `purge` ya detiene los contenedores antes de eliminarlos.

## 3. Borrado definitivo y comprobación

**Los siguientes comandos con `--apply --delete-data` destruyen datos.** Afectan
TODO el contenido del emulador seleccionado, incluidos recursos creados a mano,
no únicamente compras/stacks de este repo. Otros stacks CloudFormation detectados
impiden generar plan, pero eso no certifica ausencia de recursos manuales ajenos.

```bash
./localstack/lab cleanup purge --plan "$HOME/micro-test-cleanup.json"
./localstack/lab cleanup purge \
  --plan "$HOME/micro-test-cleanup.json" --apply --delete-data --images
```

Elimina por IDs revisados, sin `docker system prune`, `volume prune`, borrado forzado
ni reset de Docker Desktop. Revalida identidad del motor, contenedores y recursos;
rechaza nuevos usuarios ajenos. Detiene nodos antes de emulador para frenar controllers.
No elimina credenciales AWS/Docker, herramientas del sistema, fuentes ni historial Git.

Resultado: `$HOME/micro-test-cleanup.result.json`.
`SELECTED_SCOPE_COMPLETE` significa únicamente que terminó la selección.
**No certifica motor vacío ni ausencia de residuos.** Revisa `remainingContainers`,
`retained` y `bindMountsRetained`, además de redes/volúmenes no seleccionados del plan.
Si falla, queda `INCOMPLETE`; no revierte paradas previas. Conserva plan original:
repetir `purge` tolera IDs ya eliminados y no adopta contenedores nuevos con mismo nombre.
Un cambio de motor/identidad exige revisión; no edites fingerprints para evitar bloqueo.

Las imágenes públicas compartidas (LocalStack, k3s, Istio, Lambda, PostgreSQL), caché
BuildKit y archivos externos se conservan. Consulta espacio con:

```bash
docker --host unix:///var/run/docker.sock system df
```

Para retirar una imagen pública que ya no necesitas, comprueba sus usuarios y elimina
**su etiqueta concreta** con `docker image rm ETIQUETA_REVISADA`, sin `--force`.
No hay limpieza global segura de caché BuildKit compartida atribuible a este lab.
Los bind mounts externos necesitan revisión de su ruta exacta y contenido antes de
borrarlos manualmente. El script no pretende identificar todos los datos de otros proyectos.

Si ejecutaste k3d/kubectl manualmente, revisa `kubectl config get-contexts`: entradas
antiguas de este clúster en `~/.kube/config` pueden permanecer. El lanzador usa su
propio kubeconfig en `.state`; no modifica ni borra configuración global del usuario.

## 4. Archivos generados y visor

Detén observador Aspire **en el equipo donde lo arrancaste**. El visor actual está en
Mac; cleanup WSL no detiene procesos Mac. Sigue [comandos de observabilidad](observability/README.md#arrancar-o-detener)
y espera `supervisorAlive: false`. Si también arrancaste visor en WSL, detenlo allí.

Después de purgar recursos revisados, en WSL:

```bash
./localstack/lab cleanup files --plan "$HOME/micro-test-cleanup.json"
./localstack/lab cleanup files \
  --plan "$HOME/micro-test-cleanup.json" --apply --delete-data
```

Comprueba motor/recursos, rechaza emulador recreado, visor vivo, raíces con symlinks
u archivos Git dentro de rutas generadas. Borra JWT, purchase.http generado, kubeconfig,
reportes, snapshots locales, herramientas descargadas, venv y cachés Python. Copia tus
requests/evidencias antes si quieres conservarlas. Conserva marcador vacío
`.lifecycle.lock` para evitar dos operaciones simultáneas.

Plan y recibo en `$HOME` permanecen como evidencia hasta que decidas borrarlos. ZIPs,
carpetas de transferencia en `/mnt/c/mnt`, clones anteriores y archivos generados en
Mac quedan fuera del alcance: revisa y elimina sus rutas exactas por separado. No hay
proceso automático que borre todos tus checkouts o datos de otras máquinas.

Eliminar volúmenes libera espacio dentro del almacenamiento Docker; no garantiza
reducción inmediata del archivo de disco virtual en Windows. Compactación del disco
WSL/Docker Desktop es una tarea separada del host y puede afectar otros proyectos;
no se ejecuta desde este script. [Docker Desktop y WSL](https://docs.docker.com/desktop/features/wsl/).
Comportamiento Docker: [contenedores/volúmenes](https://docs.docker.com/reference/cli/docker/container/rm/)
e [imágenes/etiquetas](https://docs.docker.com/reference/cli/docker/image/rm/).

## 5. Primera corrida nuevamente

Tras revisar que no queden helpers de este lab ni puerto 4566 ocupado, sigue
[primera corrida](README.md#primera-corrida-en-entorno-limpio) en el mismo motor.
`./localstack/lab up` recreará venv y arquitectura completa; exige token de licencia.
No reconstruye compras borradas. No arranques hasta resolver residuos del lab anterior.

Recoge `bootstrap.json`, prueba de nodo Karpenter y compra/PDF. Después valida carga
CPU/RAM y persistencia. Consulta [estado de validación](VALIDATION.md) para evidencia
actual; no confundas reconstrucción descartable con recuperación de datos al reiniciar.
