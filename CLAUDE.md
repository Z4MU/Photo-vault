# PhotoVault — Guía del proyecto para Claude Code

> Este archivo es el contexto principal del proyecto. Léelo completo antes de tocar código.
> Si algo aquí contradice el código real, **el código manda**: avisa al usuario de la diferencia y actualiza este archivo.
> El plan de trabajo por fases está en **`ROADMAP.md`**. Márcalo al completar cada punto.

---

## 1. Qué es

PhotoVault es una app de escritorio (Windows) para **indexar, navegar, etiquetar y filtrar** una colección personal de fotos y videos muy grande (~172.000 archivos en la DB real, y creciendo). La idea es tener algo rápido y local que no tenga las limitaciones de los gestores genéricos.

- **Usuario / dueño:** Jose. Comunícate en **español**.
- **Entorno:** Windows, VS Code, se ejecuta con `py main.py` desde la terminal integrada.
- **Python:** 3.14, instalado en `%LOCALAPPDATA%\Python\pythoncore-3.14-64\` (**no está en el PATH**; usa `py`). `python3` en Windows es un alias de la Microsoft Store que se queda colgado: **no usarlo**.
- **Entorno virtual de desarrollo:** `.venv\` (creado con `--system-site-packages`, reutiliza PyQt6/Pillow/opencv globales; agrega pytest, ruff, mypy).
- **Repo:** GitHub `Z4MU/Photo-vault`, rama `main`. La raíz de este directorio es el repo.
  - El historial V2…V11 se reconstruyó desde las carpetas `Fotos V*/` (un commit por versión con su fecha original). Esas carpetas están en `.gitignore` y se pueden borrar.
- **Colección real:** está en `G:\Fotos\...` (disco que puede estar desconectado → ver riesgo en §11). Incluye muchos `.HEIC` de iPhone.

---

## 2. Stack

| Pieza | Uso |
|---|---|
| **PyQt6** (+ `PyQt6-Qt6`) | Toda la UI. `PyQt6-Qt6` es necesario para `QSvgWidget` (gráficas de estadísticas). |
| **SQLite** (`sqlite3` stdlib) | Base de datos en `~/.photovault/photovault.db` |
| **Pillow** | Lectura de imágenes, EXIF, generación de miniaturas |
| **opencv-python** | Duración/dimensiones de video y frame para miniatura |
| **pillow-heif** | Soporte `.heic` / `.heif` (la colección tiene muchos). Se registra con `try/import`; si faltara, los HEIC se indexan sin miniatura |
| **Send2Trash** | Mandar archivos a la Papelera de reciclaje (duplicados). Nunca usar `unlink` sobre fotos del usuario |
| **PyInstaller** | Empaquetado a `PhotoVault.exe` |
| **pytest / ruff / mypy** | Desarrollo (`requirements-dev.txt`, config en `pyproject.toml`) |

Datos del usuario (fuera del repo, nunca commitear):
- `~/.photovault/photovault.db` — base de datos (~33 MB)
- `~/.photovault/thumbs/<2 chars>/<sha1>.jpg` — caché de miniaturas
- `~/.photovault/backups/` — copias de seguridad automáticas (§6)
- `~/.photovault/logs/photovault.log` — log rotativo (§7)

---

## 3. Estructura de archivos

```
main.py             Solo el arranque: main() + _startup() (backup, migraciones, papelera)
config.py           Rutas de datos, versión, tamaños de miniatura, paleta (COLORS)
services.py         Lógica de negocio. La UI SOLO habla con esta capa.
database.py         Acceso a SQLite: conexiones, migraciones versionadas, queries
models.py           Dataclasses tipadas + enums de ordenamiento
indexer.py          Escaneo de carpetas, extracción de fecha/dimensiones/duración
thumbnail_cache.py  Caché persistente de miniaturas en disco
backup.py           Backups rotativos de la DB (diario + pre-migración)
xmp_sidecar.py      Leer/escribir etiquetas en archivos .xmp junto a las fotos
logging_setup.py    Logging a archivo + captura global de excepciones
ui/
  style.py + dark.qss   Tema oscuro (el QSS es un archivo aparte, va en `datas` del .spec)
  workers.py            QThreads, ImageLoadQueue, retire_thread, TaskWorker, run_with_progress
  widgets.py            TaskStatusWidget, PhotoThumbnail, ClickableRow, clear_layout, layout_widgets
  images.py             load_preview_pixmap, load_full_image, is_animated
  charts.py             build_bar_chart_svg (sin Qt, testeable)
  gallery.py            GalleryModel, GalleryDelegate, GalleryView (galería virtualizada)
  sidebar.py            TagFilterPanel, FolderTreePanel, TimelinePanel
  viewer.py             ViewerWindow, ImageView (visor a pantalla completa)
  video_player.py       VideoPlayer (QtMultimedia)
  photo_info.py         InfoPanel (panel de información del visor)
  system.py             Explorador, abrir con la app, portapapeles
  main_window.py        MainWindow
  dialogs/
    photo.py            TagEditor (etiquetas de una foto), BulkTagDialog
    stats.py            StatsDialog
    duplicates.py       DuplicatesDialog
    quick_tag.py        QuickTagSetupDialog, QuickTagWindow
    settings.py         SettingsDialog
    tags.py             CategoryManagerDialog, EditTagDialog, TagManagerDialog
    folders.py          IndexDialog, DeindexDialog, RelocateDialog, TrashDialog
tests/              Suite de pytest (conftest.py aísla DB, backups y caché en tmp)
pyproject.toml      Config de pytest, ruff (lint + format) y mypy
requirements.txt    Dependencias de la app
requirements-dev.txt Dependencias de desarrollo (pytest, pytest-cov, ruff, mypy)
PhotoVault.spec     Configuración de PyInstaller
build.bat           Script de build para Windows (autodetecta Python)
ROADMAP.md          Plan por fases con casillas
```

Al agregar UI nueva: un diálogo por archivo/área en `ui/dialogs/`; widgets reutilizables en `ui/widgets.py`; nada de lógica de negocio ni SQL en `ui/`. Ningún archivo debería pasar de ~600 líneas.

### Flujo de capas (respetar siempre)

```
main.py → ui/ → services.py → database.py → SQLite
                     ↓              ↓
              thumbnail_cache   backup.py
              indexer.py
              xmp_sidecar.py
(config.py lo usan todos; no importa nada del proyecto)
```

**Reglas:**
- La UI **no** escribe SQL ni llama a `database` directamente. Excepciones existentes y permitidas: `db.init_db()`, `db.close_connection()` (workers), `db.DB_PATH`, `db.SCHEMA_VERSION` y `db.DatabaseTooNewError` (en `main.py`).
- `database.py` devuelve **modelos** (`Photo`, `Tag`, `Stats`…), nunca `sqlite3.Row` hacia afuera.
- La lógica que combina varias queries (p. ej. obtener tags ocultos + contar + paginar) vive en `services.py`. `services.py` no tiene SQL.
- `main.py` importa arriba **solo** `logging_setup` (que solo importa `config`). Todo lo demás se importa dentro de `main()`/`_startup()`, después de `setup_logging()`, para que un fallo al importar quede en el log (test: `test_startup.py`).

---

## 4. Modelos (`models.py`)

- `Photo` — `id, path, filename, year, month, media_type ("image"|"video"), duration, filesize, width, height, added_at, md5, mtime`. `mtime` es el del archivo al indexarlo (None = registro de antes de v3 aún no re-indexado). Propiedades: `is_video`, `duration_str` (`M:SS`), `short_name` (truncado a 22 chars). `md5` solo se carga en `get_photo_by_id` y `get_all_photos_for_duplicates`. Las queries usan `_PHOTO_COLUMNS`.
- `Tag` — `id, name, category, color, hidden, sidebar_hidden`.
- `GalleryPage` — `photos, total, offset, limit, sort_field, sort_order` + `page_number`, `total_pages`, `has_prev`, `has_next`.
- `Stats` — `total_photos, total_tags, years, by_month, by_type, top_tags`.
- `DuplicateGroup` — `md5, photos` + `size`, `wasted_bytes`.
- `SortField` (`DATE`, `FILENAME`, `FILESIZE`, `ADDED_AT`) y `SortOrder` (`ASC`, `DESC`).
- `sort_to_sql(field, order)` traduce a SQL usando el **allowlist `_SORT_SQL`**. Nunca interpolar ordenamiento desde input del usuario.
  - Todo orden termina en `p.id` (paginación estable con nombres/tamaños repetidos) y ASC/DESC son exactamente inversos, para que un índice sirva a ambos. Fecha: `year, month, filename, id`.

---

## 5. Base de datos (`database.py`)

### Conexiones
- **Una conexión por hilo** con `threading.local()` (`get_connection()`).
- PRAGMAs: `journal_mode=WAL`, `foreign_keys=ON`, `cache_size=-32000`, `synchronous=NORMAL` (seguro con WAL; mucho más rápido al escribir), `temp_store=MEMORY`.
- Todo hilo worker (`QThread`) debe llamar `db.close_connection()` al terminar (ya lo hacen `IndexWorker`, `ImageLoadQueue`, `MD5Worker`, `TaskWorker`).
- Escrituras con `with transaction() as conn:` (commit/rollback automático).

### Esquema (versión 3)

```sql
photos(id PK, path UNIQUE, filename, media_type, year, month, filesize,
       width, height, duration, md5, added_at,
       mtime REAL,                                   -- v3: indexación incremental
       folder GENERATED ALWAYS AS (rtrim(path, replace(path,'\',''))) VIRTUAL)  -- v3
tags(id PK, name UNIQUE COLLATE NOCASE, category, color, hidden, sidebar_hidden)
photo_tags(photo_id → photos ON DELETE CASCADE, tag_id → tags ON DELETE CASCADE,
           PK(photo_id, tag_id))
categories(name PK COLLATE NOCASE)
app_settings(key PK, value)      -- flags internos y preferencias
deleted_photos(id PK, batch_id, reason, deleted_at, path,   -- v2: papelera interna
               photo_json, tags_json)
```

Índices: `photos(year, month, filename)` (`idx_photos_date`), `photos(filename)`, `photos(filesize)`, `photos(added_at)`, `photos(folder)`, `photos(md5)`, `photo_tags(photo_id)`, `photo_tags(tag_id)`, `deleted_photos(batch_id)`, `deleted_photos(deleted_at)`. (v3 quitó `idx_photos_year`/`idx_photos_month`, cubiertos por el compuesto.)

Migraciones: v1 `_m001_baseline`, v2 `_m002_internal_trash`, v3 `_m003_mtime_and_sort_indexes` (≈1 s sobre la DB real).

- `photos.folder` es una columna **calculada** (no se escribe nunca): la carpeta de la foto con la `\` final. `PRAGMA table_info` no la muestra; `_columns()` usa `table_xinfo`.
- El `upsert` pone `md5 = NULL` si cambió el tamaño o el `mtime` (antes un md5 viejo sobrevivía a cambios del archivo).
(La DB real tiene además una columna sobrante `photos.sidebar_hidden` de alguna versión vieja; es inofensiva.)

- `tags.hidden = 1` → las fotos con ese tag **no aparecen** en la galería.
- `tags.sidebar_hidden = 1` → el tag no aparece en el panel de filtros.
- ⚠️ `ON DELETE CASCADE`: borrar una fila de `photos` borra sus etiquetas. Por eso **todo borrado de fotos pasa por `db.delete_photos(ids, reason)`**, que antes copia registro + etiquetas a `deleted_photos` en la misma transacción. Nunca hacer `DELETE FROM photos` directo (excepción: `apply_relocation` al fusionar, donde las etiquetas ya se pasaron al otro registro).

### Papelera interna
- `delete_photos(ids, reason)`: un lote (`batch_id` uuid) por operación; `reason` es texto para el usuario ("Carpeta des-indexada: …").
- `restore_trash_batch(batch_id)` → `(restaurados, fusionados)`: si la ruta ya volvió a indexarse, le suma las etiquetas; los tags borrados se recrean con la categoría/color **del momento del borrado**.
- `services.purge_old_trash()` corre en `_startup()` y elimina lo que tenga más de `TRASH_KEEP_DAYS` (30) días.
- No guarda tags borrados con "Eliminar etiqueta" (eso se confirma mostrando cuántas fotos la tienen).

### Reubicar (cambiar prefijo de ruta)
- `get_relocation_plan(old, new)` → `[(photo_id, ruta_nueva, id_existente)]`; `apply_relocation(plan)` → `(movidos, fusionados)`.
- `services.preview_relocation()` revisa en disco una muestra de 25 rutas nuevas (`looks_right` si existen ≥ 50 %) para detectar errores de tipeo. La UI exige vista previa antes de aplicar y pide confirmación extra si `looks_right` es falso.
- Si la ruta nueva ya estaba indexada (se re-indexó), se fusionan: etiquetas al registro existente y el viejo se quita.

### Filtros de consulta
Para la galería continua: `get_photo_ids(filtro, orden, limit, offset)` (solo ids, mismo orden que `get_photos`), `count_before_date(filtro, año, mes, orden)` (fila a la que saltar; `mes=None` = el año, `NO_MONTH` = las de ese año sin mes; respeta dónde pone SQLite los NULL en ASC/DESC) y `get_date_histogram(filtro)`.

`get_photos()` / `get_photo_count()` aceptan: `tag_ids` (AND), `hidden_tag_ids` (exclusión), `search` (LIKE en filename), `folder` (carpeta y subcarpetas), `untagged_only`, más `limit/offset/sort_field/sort_order`. Ambas arman el WHERE con **`_build_where(PhotoFilter(...))`**: un filtro nuevo se agrega ahí (una sola vez) y en `PhotoFilter`.

**Filtro de carpeta:** siempre con `folder_like_pattern(folder)` + `LIKE ? ESCAPE '!'`. Normaliza la ruta, agrega el separador final (`D:\Fotos` no incluye `D:\Fotos2`) y escapa `%`/`_`. El escape es `!` porque `\` es el separador de Windows. Nunca volver a `LIKE folder + '%'`.

### Configuración del usuario
`get_setting(key, default)` / `set_setting(key, value)` sobre `app_settings`. Claves en uso: `seeded`, `thumb_size` (slider de la galería, 100–400), `xmp_sidecars` (`"1"` = activado). (`page_size` quedó sin uso desde la fase 5.)

### Migraciones versionadas
La versión del esquema vive en **`PRAGMA user_version`**. `init_db()` corre en **cada arranque**:
1. Si `user_version > SCHEMA_VERSION` → lanza `DatabaseTooNewError` **sin tocar nada** (exe viejo abriendo DB nueva).
2. Si hay migraciones pendientes y la DB ya tenía tablas → **backup obligatorio** (`backup.pre_migration_backup`). Si el backup falla, se lanza la excepción y **no se migra**.
3. Aplica cada migración pendiente en su propia transacción (`BEGIN` → migración → `PRAGMA user_version = N` → `COMMIT`; rollback si falla).
4. Seed inicial (una sola vez, flag `app_settings.seeded`).

**Para agregar una migración:**
- Escribir `def _m00N_descripcion(conn):` con docstring (la primera línea va al log) y agregarla **al final** de `_MIGRATIONS`. `SCHEMA_VERSION` se calcula solo.
- Usar `conn.execute(...)`, **nunca `executescript`** (hace COMMIT implícito y rompe la atomicidad).
- Columnas nuevas con `_add_column_if_missing(conn, tabla, col, typedef)`.
- **Nunca modificar una migración ya publicada**, siempre agregar una nueva.
- Agregar un test en `tests/test_migrations.py`.

**Reglas aprendidas a la mala (siguen vigentes):**
1. Un índice sobre una columna nueva se crea DESPUÉS de agregar la columna (causó `no such column: md5`).
2. **Datos semilla se insertan UNA sola vez** (flag `seeded`). Nunca poner `INSERT OR IGNORE` de seeds en cada arranque: hacía que lo que el usuario borraba reapareciera.
3. DBs anteriores a `app_settings` que ya tienen tags se marcan como `seeded` (lo hace `_m001_baseline`).
4. `_m001_baseline` es idempotente y equivale a todo lo que hacía `init_db()` hasta V11: lleva cualquier DB vieja (V1…V11, `user_version = 0`) a la versión 1. Verificado contra una copia de la DB real (171.840 fotos): solo cambia `user_version`.

---

## 6. Backups (`backup.py`)

- **Diario:** `_startup()` llama `backup.daily_backup(db.DB_PATH)` antes de `init_db()`. Crea una copia si no hay una de hoy; conserva las últimas 7. **Nunca lanza excepciones** (un fallo no impide abrir la app; queda en el log).
- **Pre-migración:** `init_db()` lo crea antes de cambiar el esquema; conserva las últimas 3. **Sí lanza** si falla.
- Usa `sqlite3.Connection.backup()` (seguro con WAL y con la DB abierta). Escribe a `.tmp` y renombra: nunca queda una copia a medias con nombre válido.
- Nombres: `photovault-diario-YYYYMMDD-HHMMSS.db`, `photovault-premigracion-YYYYMMDD-HHMMSS-a-vN.db`.
- Restaurar a mano: cerrar la app y copiar el backup sobre `~/.photovault/photovault.db` (borrando `-wal` y `-shm`).

---

## 7. Logging (`logging_setup.py`)

- `setup_logging()` (en `__main__`, antes de crear la `QApplication`): archivo rotativo `~/.photovault/logs/photovault.log` (5 × 1 MB) + consola si existe (`sys.stderr` es `None` en el .exe).
- Instala `sys.excepthook` y `threading.excepthook`: toda excepción no manejada queda en el log y, si ocurre en el hilo de UI, se muestra un `QMessageBox`. Con un excepthook propio, PyQt6 no aborta la app por excepciones en slots.
- `install_qt_handlers()` redirige los mensajes internos de Qt (p. ej. `QThread: Destroyed while thread is still running`) al logger `qt`.
- Cada módulo usa `logger = logging.getLogger(__name__)`. **No usar `print`** ni `except: pass`: registrar con `logger.warning/exception` y capturar excepciones concretas cuando se pueda.
- `thumbnail_cache` registra cada miniatura fallida **una sola vez por sesión** (`_log_failure`) para no inundar el log.

---

## 8. Indexación (`indexer.py`)

- `index_folder(folder, progress_callback, should_stop, force=False)` → `IndexResult(added, updated, unchanged, errors, cancelled, new_ids)`.
- **Listado:** `scan_media_files()` con `os.scandir` (en Windows el tamaño y el mtime vienen en el listado; antes `rglob`+`is_file`+`stat`). Las rutas salen con el mismo formato que `str(Path(...))`, aunque la carpeta llegue con `/` (test: `test_rutas_iguales_a_las_de_versiones_anteriores`). **Si cambia el formato de las rutas, la DB se duplicaría.**
- **Incremental:** con `get_index_state(folder)` → `{ruta: (tamaño, mtime, año)}`; mismo tamaño y mtime = `unchanged`, no se abre el archivo. `force=True` relee todo. Un registro con año imposible (> año actual + 1, de reglas viejas) se relee aunque no haya cambiado.
- **Registros de antes de v3** (`mtime` NULL) con el mismo tamaño: solo se les anota el mtime (`set_mtimes`), sin abrir el archivo. Primera re-indexación de `G:\Fotos` (171.840): ~2 s; sin esto, ~90 min.
- **Una sola apertura por imagen** (`_read_image_info`: EXIF + tamaño); escritura en lotes de `BATCH_SIZE` (500) con `upsert_photos` en una transacción por lote. Al cancelar se guarda lo leído.
- Medido con la colección real: archivo sin cambios 0,06 ms; releído 2,6 ms (antes 31 ms en frío).
- Fecha, en este orden: EXIF (solo imágenes) → nombre del archivo → nombre de la carpeta padre → `mtime`.
  - EXIF con prioridad: `DateTimeOriginal` (IFD Exif) → `DateTimeDigitized` → `DateTime` (IFD0, es la fecha de *modificación*: último recurso). Se usa `img.getexif()` + `get_ifd(0x8769)`, no `_getexif()`.
  - Las fechas ya guardadas con la regla vieja se corrigen al re-indexar la carpeta.
- `DATE_PATTERNS` está compilado y es estricto a propósito (tests en `tests/test_dates.py`):
  - El patrón compacto `YYYYMMDD` usa `(?<!\d)...(?!\d)` para **no** atrapar resoluciones (`1920x1080`, `19201080`).
  - Año válido 1990 – año actual + 1 (`max_plausible_year()`). Mes fuera de 1–12 → se guarda solo el año.
  - En los patrones compactos (8 dígitos) un mes o día inválido descarta la coincidencia y se busca la siguiente: así UUIDs como `20547205-28df-…` no dan el año 2054.
  - Soporta `IMG_/VID_YYYYMMDD` y WhatsApp `IMG-YYYYMMDD-WA0001`.
- `pillow-heif` se registra con `try/import` al cargar el módulo.
- `extract_video_thumbnail()` solo delega a `thumbnail_cache.get_video_thumbnail()` (compatibilidad).
- El MD5 **no** se calcula al indexar (sería muy lento); se calcula bajo demanda desde el diálogo de duplicados.

---

## 9. Miniaturas (`thumbnail_cache.py`)

- Nombre: `[v_]<sha1(path::mtime::THUMB_VERSION)>_<size>.jpg`, en la subcarpeta `<2 primeros chars del hash>`.
  - Si el original cambia (mtime), la miniatura se regenera sola.
  - Cada tamaño (100 duplicados, 200 galería, 480 etiquetado rápido/videos) es un archivo distinto.
  - **`THUMB_VERSION`**: subirlo cuando cambie la forma de generar miniaturas → invalida todo el caché. v2 = orientación EXIF + tamaño en el nombre.
- Aplica `ImageOps.exif_transpose` (orientación EXIF de fotos de celular).
- API: `get_thumbnail(path, size, mtime=None)`, `get_video_thumbnail(...)`, **`get_photo_thumbnail(photo, size)`** (elige imagen/video y usa `photo.mtime`), `is_cached(...)`, `purge_orphans([(ruta, mtime)])`, `cache_size_mb()`, `CACHE_DIR`.
- **Pasar siempre el `mtime` de la DB** (o usar `get_photo_thumbnail`): sin él se consulta el archivo en el disco de la colección por cada miniatura. Con él, una miniatura en caché tarda 0,17 ms (antes 5,6 ms). Con el mismo mtime la clave es idéntica a la de antes, así que el caché existente sigue valiendo.
- `services.pregenerate_thumbnails(ids | None)`: genera las que falten con `THUMB_WORKERS` hilos (Pillow suelta el GIL: ×2,3 medido), por tandas cancelables; la comprobación de caché también va dentro de los hilos.
- `purge_orphans` borra también las miniaturas del formato anterior (sin `_<size>`).

---

## 10. UI (`ui/`)

### Arranque (`main.py`)
`main()` → `setup_logging()` → imports (si fallan: log + código 2) → `QApplication` → `install_qt_handlers()` → `_startup()` (backup diario + `init_db()` + purgar papelera; si falla muestra el error y sale con código 1) → `MainWindow`.

### Helpers de `ui/widgets.py` (usar siempre)
- `clear_layout(layout)`: vacía un layout **incluidos los espaciadores** (antes se acumulaban los `addStretch()` en cada refresco). No escribir el bucle `reversed(range(count()))` a mano.
- `layout_widgets(layout)`: widgets directos de un layout, sin `None`.
- `ClickableRow`: `QWidget` con señal `clicked`. No reasignar `mousePressEvent` con lambdas.
- No llamar `self.scroll` a un atributo de un widget: tapa `QWidget.scroll()`. Los diálogos usan `self.scroll_box`.

### Hilos
Todos heredan de `StoppableThread` (`stop()`, `is_stopping()`):
- `IndexWorker` — indexación; `completed(IndexResult)`.
- `ImageLoadQueue(load_fn, workers, max_pending)` — cola **persistente** de imágenes: `request(clave, arg)` desde la UI, emite `loaded(clave, QImage)` (nula = falló). **LIFO**: lo último que se pidió (lo que está en pantalla) va primero; pasado `max_pending` descarta lo más viejo y `request` devuelve esas claves. `thumbnail_queue()` = la de la galería (`THUMB_WORKERS` hilos); el visor tiene otra de 2 hilos para fotos completas.
- `MD5Worker` — hashes faltantes; `completed(n)`.
- `MissingFilesWorker` — busca archivos faltantes; `completed(MissingReport)`. No borra nada.

**Reglas de hilos (causaron crashes `QThread: Destroyed while thread is still running`):**
- Un worker **nunca** crea `QPixmap` ni widgets: emite `QImage`/datos y el `QPixmap` se crea en el slot (hilo de UI).
- **Nunca** redefinir la señal `finished` de `QThread` (tapa la original); para resultados usar `completed`.
- Para descartar un worker que sigue corriendo: `disconnect_all(señales…)` → `retire_thread(w)`. Pide que pare y guarda la referencia en `_retired_threads` hasta que emita `finished`: no bloquea la UI y Python nunca destruye un hilo vivo. `_stop_loader()` hace esto con el loader de la galería.
- Los diálogos con workers (`DuplicatesDialog`, `IndexDialog`, `DeindexDialog`) sobreescriben `done()` (se llama al cerrar por cualquier vía: botón, Esc, X) para retirar su worker. `IndexDialog` pregunta antes de cancelar.
- `MainWindow.closeEvent` detiene timers, retira el loader y llama `wait_all_threads()`.
- Las funciones lentas de `services`/`indexer` aceptan `should_stop` para que el worker pueda cancelarlas.
- No llamar una señal propia `done` en una subclase de `QDialog` (choca con `QDialog.done`). Por eso `QuickTagWindow` usa `done_signal`.
- Conectar señales de workers a **métodos** (`_on_progress`, `_on_error`…), no a lambdas que devuelven tuplas `(a(), b())`.
- Cuando un worker avisa que terminó (`completed`), el hilo **todavía** está cerrando su conexión: soltarlo con `retire_thread(w)`, no con `self._w = None` (`MainWindow._release_background`, `InfoPanel._on_details`).

### Imágenes grandes
`load_preview_pixmap(path, max_side)`: `QImageReader` con `setAutoTransform(True)` (orientación EXIF) y `setScaledSize` (no carga el original completo). Si Qt no puede leer el formato (HEIC), usa la miniatura de Pillow. Usarla en vez de `QPixmap(path)`.

### Texto del usuario en la UI
Nombres de archivo, tags y rutas pueden traer `<`, `&`… En `QLabel` con HTML usar `html.escape()`; en labels de texto plano poner `setTextFormat(Qt.TextFormat.PlainText)` (si no, Qt adivina y puede interpretarlo como HTML). El SVG de estadísticas se arma en `build_bar_chart_svg()`, que escapa.

### Galería virtualizada (`ui/gallery.py`, fase 5)
- **Sin páginas**: `GalleryView` (`QListView` en `ListMode` + `setWrapping` + `setUniformItemSizes(True)`) muestra toda la consulta en un scroll. Con tamaños uniformes Qt solo pregunta por las filas visibles.
- `GalleryModel` solo conoce el total (`services.count_gallery`); las fotos se leen por tramos de `CHUNK_SIZE` (500) al pedir una fila y se guardan los últimos `MAX_CHUNKS` (40). `photo_at(row)`, `row_of(id)`, `ids_in_ranges([(ini, fin)])` (rangos grandes van por `get_gallery_ids`, sin cargar fotos).
- Las miniaturas se piden a la `ImageLoadQueue` **cuando el delegate pinta la celda** (solo lo visible) y quedan en `QPixmapCache` (200 MB, clave `thumb:<id>:<mtime>:<tamaño>`). Las que fallan no se vuelven a pedir (⚠). El resultado de una consulta anterior se ignora.
- `GalleryDelegate` pinta todo (miniatura, nombre, ▶ y duración, selección); no hay un widget por foto. Slider 100–400 px (`services.get/set_thumb_display_size`); ≥ 240 usa las miniaturas de 480 (`thumb_source_size`).
- Selección `ExtendedSelection` (clic, Ctrl, Shift, Ctrl+A). `visualRegionForSelection` devuelve el viewport: Qt calculaba el rectángulo de cada fila seleccionada (1,2 s con Ctrl+A).
- Arrastrar a otra app: `mimeData` con las URLs; más de `MAX_DRAG` (500) se rechaza con aviso.
- `reload_keep_position()` recarga la misma consulta sin perder el lugar (tras etiquetar, indexar…); `_apply_query()` es para filtros/orden nuevos (vuelve arriba).
- **Búsqueda con debounce** de 300 ms (`SEARCH_DEBOUNCE_MS`); Enter busca ya.
- Sidebar: `services.get_totals()` (2 COUNT) en lugar de `get_stats()` completo.

### Sidebar (`ui/sidebar.py`)
Pestañas **Etiquetas** (`TagFilterPanel`, filtro AND), **Carpetas** (`FolderTreePanel`, árbol de `services.get_folder_tree()`; un clic filtra y aparece un chip 📁 ✕ arriba) y **Fechas** (`TimelinePanel`, `services.get_date_histogram` con los filtros actuales; se recalcula solo si la pestaña está visible). Clic en un año/mes → `go_to_date` (cambia a orden por fecha si hace falta y selecciona la primera foto).

### Visor (`ui/viewer.py`)
- `ViewerWindow(source, fila)` recorre cualquier `PhotoSequence` (`count()` + `photo_at()`; `GalleryModel` lo cumple, `ListSequence` para listas). Pantalla completa por defecto; al cerrar, la galería selecciona `current_row` y recarga si `tags_were_changed`.
- Fotos: `ImageView` (`QGraphicsView`) con zoom (rueda, +/−, 1:1, ajustar), arrastre y rotación de **solo la vista**. Decodifica hasta `config.VIEWER_MAX_SIDE` (6000) en su propia `ImageLoadQueue` con `load_full_image` (Pillow si Qt no puede: HEIC). Mientras carga muestra la miniatura de `QPixmapCache`; guarda 3 imágenes y precarga la anterior y la siguiente.
- GIF/WebP animados con `QMovie` (`is_animated`). Videos con `VideoPlayer` (QtMultimedia, backend FFmpeg del wheel de PyQt6; si falla, botón "Abrir con…").
- `InfoPanel` (tecla I): datos de la DB, EXIF de cámara leído en un `TaskWorker` (`services.get_photo_details` → `indexer.read_exif_details`) y `TagEditor`. Si la DB no tiene la resolución (HEIC viejos) usa la de la imagen cargada.
- Atajos en `SHORTCUTS_HELP` (F1 los muestra junto con los de la galería). Son `QShortcut` del diálogo: un `QLineEdit` con foco (agregar etiqueta) se queda con las letras y flechas.
- Los botones de la barra y el `ImageView` son `NoFocus`: si no, las flechas moverían el scroll en vez de cambiar de foto.

### Tareas en segundo plano (`MainWindow`)
- Una a la vez (`_bg_worker`, `_bg_kind` = `"index"` | `"thumbs"`), con progreso y **Cancelar** en la barra de estado (`TaskStatusWidget`). Se puede seguir usando la app.
- `start_indexing(folder)`: `IndexDialog` solo elige la carpeta y emite `start_requested`. Al terminar recarga y, si hubo fotos nuevas, lanza `start_thumbnail_generation(new_ids)`. La indexación tiene prioridad: cancela una generación de miniaturas en curso.
- `start_thumbnail_generation(None)` = toda la colección (botón *Generar todas* en Configuración); devuelve False si hay otra tarea.
- Al cerrar con una indexación en curso, pregunta; lo ya indexado se conserva.

### Ventana principal (`MainWindow`)
- Sidebar: botones de acciones, botón destacado **⚡ Etiquetado rápido**, filtros por tag agrupados por categoría (colapsables), botón 👁 para esconder tags del sidebar, botón **"👁 Mostrar escondidas (N)"** (solo visible si hay escondidas) que las muestra en cursiva con 🚫 para restaurarlas, estadísticas.
- Barra superior: búsqueda por nombre, ordenamiento (campo + dirección), contador, botón **Seleccionar** (modo selección múltiple) y **Etiquetar selección**.
- Filtro por tags es **AND** (la foto debe tener todos los seleccionados).

### Diálogos
| Clase | Qué hace |
|---|---|
| `ViewerWindow` | Visor (ver arriba). Reemplaza al antiguo `PhotoDetailDialog`. |
| `BulkTagDialog` | Agregar/quitar un tag a todas las fotos seleccionadas. |
| `QuickTagSetupDialog` | Elegir conjunto a etiquetar: carpeta indexada + tags requeridos + "solo sin etiquetar", con conteo en vivo. |
| `QuickTagWindow` | Etiquetado por teclado: `←/→` navegar, `Space` saltar, `1–9` atajos de tag, `Ctrl+Z` deshacer (historial completo), búsqueda de tags, `Esc` salir. Al terminar la última foto regresa a la galería y esta se recarga. |
| `StatsDialog` | Tarjetas de totales + barras SVG por año y top 10 tags. |
| `DuplicatesDialog` | Calcula MD5 en hilo (cancelable) **solo de archivos cuyo tamaño se repite** (38 % de la colección real), agrupa duplicados, manda copias a la **Papelera** (con confirmación, nunca la última copia). |
| `SettingsDialog` | Tamaño de caché, limpiar caché, purgar huérfanos. |
| `TagManagerDialog` | Crear/editar (✎ → `EditTagDialog`)/eliminar tags (confirma con el n.º de fotos), flags hidden, exportar/importar JSON. |
| `EditTagDialog` | Cambiar nombre, categoría y color; avisa si el nombre ya existe (`TagNameConflictError`). |
| `CategoryManagerDialog` | Crear/renombrar/eliminar categorías (al eliminar, sus tags pasan a `general`). |
| `IndexDialog` | Solo elige la carpeta y emite `start_requested(folder)`; la indexación corre en `MainWindow` (segundo plano). Con `busy=True` deshabilita el botón. |
| `DeindexDialog` ("🗂 Carpetas") | Lista de carpetas con **Reubicar** y **Eliminar** (a la papelera; confirma con n.º de registros/tags); buscar archivos faltantes (**buscar en hilo → resumen → confirmar → papelera**); botones *Reubicar carpeta o unidad…* y *Papelera de PhotoVault (N)*. |
| `RelocateDialog` | Ruta vieja (combo con las unidades indexadas y si están disponibles) → ruta nueva; vista previa obligatoria con comprobación en disco. |
| `TrashDialog` | Lotes de la papelera interna: restaurar, eliminar lote, vaciar. |
| `SettingsDialog` | (además) opción `.xmp`, *Escribir .xmp ahora*, *Importar desde .xmp*, *Generar todas* (miniaturas en segundo plano); *Purgar huérfanos* corre con `run_with_progress`. |

### Archivos faltantes — protección contra pérdida de datos
`services.find_missing_files()` agrupa por unidad (`Path.anchor`) y **no incluye**:
- unidades que no existen (disco desconectado), ni
- unidades donde faltan **todos** los archivos y son ≥ `SUSPICIOUS_ROOT_MIN` (20): probablemente otro disco con la misma letra.

Esas van en `report.skipped` y se muestran al usuario. Si de verdad ya no existen, se quitan con "Eliminar" en su carpeta. `delete_missing(report)` solo borra `report.missing_ids`. Nunca volver a un "buscar y borrar" en un solo paso.

### Estilo visual
- Tema oscuro único: `ui/dark.qss`, cargado por `ui/style.py` como `DARK_STYLE` (si el archivo faltara, la app abre sin estilos y lo registra en el log).
- Paleta en `config.COLORS`: fondo `#0D0D1A`, paneles `#13131F` / `#1E1E2E`, bordes `#2D2D3F` / `#3A3A5A`, acento `#4A9EFF`, peligro `#FF4A4A`, advertencia `#FFD700`, éxito `#4AFF9E`.
- Mantener esta paleta en cualquier UI nueva. (Los estilos en línea existentes todavía usan los hex literales.)

### Formato de exportación de tags (JSON, versión 2)
```json
{ "version": 2, "app": "PhotoVault", "exported_at": "2026-10-04T12:00:00",
  "tags": [{"name": "...", "category": "...", "color": "#RRGGBB", "hidden": false, "sidebar_hidden": false}],
  "categories": ["..."],
  "assignments": [{"path": "G:\\...\\a.jpg", "filename": "a.jpg", "filesize": 123, "tags": ["x", "y"]}] }
```
- Se sigue aceptando la versión 1 (sin `assignments`).
- La importación solo crea tags que no existan (nunca sobrescribe) y **solo agrega** asignaciones, nunca quita.
- Emparejamiento de fotos: ruta exacta → si no, `(nombre, tamaño)` cuando hay **una sola** coincidencia (cambio de unidad/carpeta). Si hay varias, no adivina (`photos_missing`).
- La UI pregunta si importar las asignaciones (Sí / No / Cancelar).

### Sidecars XMP (`xmp_sidecar.py`)
- Opcional (Configuración, desactivado por defecto). Con la opción activa, `services._sync_sidecars(ids)` se llama tras **cada** función que cambia etiquetas (`add_tag`, `add_tag_by_id`, `remove_tag`, `bulk_*`, `update_tag`, `delete_tag`, `rename/delete_category`, `import_tags`). Si agregas otra función que cambie etiquetas, llama `_sync_sidecars` también.
- Escribe `<foto>.<ext>.xmp` (digiKam/darktable); lee también `<foto>.xmp` (Lightroom). `dc:subject` + `lr:hierarchicalSubject` (`categoria|tag`).
- Marca `photovault:managed="True"`: **solo se modifican/borran sidecars propios**; uno de otro programa nunca se toca (`SKIPPED_FOREIGN`). Sin etiquetas → se borra el propio.
- No crea carpetas (si la de la foto no existe → `ERROR`, p. ej. disco desconectado). Escribe a `.tmp` y renombra.
- `sync_all_sidecars` (escribir todo) e `import_from_sidecars` (leer los .xmp de todas las fotos indexadas, propios o ajenos) corren con `run_with_progress`.
- ⚠️ Privacidad: los nombres de todas las etiquetas (también las de contenido oculto) quedan visibles junto a las fotos; la UI lo advierte.

### Tareas largas genéricas
`run_with_progress(parent, título, texto, fn)` corre `fn(progress_callback=, should_stop=)` en un `TaskWorker` con `QProgressDialog` cancelable y un `QEventLoop` (la UI sigue respondiendo). Devuelve el resultado (parcial si se canceló) o `None` si hubo error (ya mostrado). Úsalo para nuevas funciones de `services` que sigan esa firma.

---

## 11. Desarrollo, build y verificación

```powershell
# Ejecutar en desarrollo
py main.py

# Entorno de desarrollo (una vez)
py -m venv --system-site-packages .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# Verificación (correr SIEMPRE antes de commitear; todo debe dar 0 errores)
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check
.\.venv\Scripts\ruff.exe format --check
.\.venv\Scripts\mypy.exe

# Cobertura (opcional)
.\.venv\Scripts\python.exe -m pytest -q --cov=database --cov=services --cov-report=term-missing

# Generar el .exe (o doble clic en build.bat)
py -m PyInstaller PhotoVault.spec --noconfirm
```

- Salida: `dist\PhotoVault.exe` (un solo archivo, `console=False`, UPX activado, ~150 MB).
- Archivos que no son `.py` (hoy: `ui/dark.qss`) van en `datas` del `.spec`; si no, el `.exe` no los encuentra.
- ⚠️ Al probar el `.exe` desde un script: en modo un-solo-archivo el `.exe` lanza un **proceso hijo**. Matar solo el padre deja el hijo vivo (y bloquea `dist\PhotoVault.exe` para la próxima compilación). Cerrar con `taskkill /PID <pid> /T /F`.
- `build.bat` busca Python en: PATH → `py` → `%LOCALAPPDATA%\Python\pythoncore-*` → instalaciones típicas.
- Si el `.exe` abre y se cierra: revisar `~/.photovault/logs/photovault.log`. Si no hay nada, poner `console=True` en el `.spec`, recompilar y ver el traceback.
- `hiddenimports` incluye `PyQt6.QtSvg`, `PyQt6.QtSvgWidgets`, `cv2`, `pillow_heif`, `send2trash.win(.legacy)`. Si agregas un import dinámico nuevo, añádelo ahí.
- `build/`, `dist/`, `.venv/` no se commitean.

### Tests
- `tests/conftest.py` tiene una fixture `autouse` que redirige `database.DB_PATH`, `backup.BACKUP_DIR` y `thumbnail_cache.CACHE_DIR` a `tmp_path`. **Ningún test debe tocar `~/.photovault`.**
- `make_legacy_db(path)` simula una DB de V1 para probar migraciones.
- Para probar contra datos reales: copiar la DB real con la API de backup (abriéndola `?mode=ro`) a un directorio temporal y apuntar `DB_PATH` ahí. Nunca contra la DB real.
- Si tocas `DATE_PATTERNS`: agregar casos válidos y falsos positivos en `tests/test_dates.py`.
- `tests/test_ui_helpers.py` usa `QT_QPA_PLATFORM=offscreen` para probar la UI sin ventanas; incluye un test que importa **todos** los módulos de `ui/` (detecta imports rotos o circulares).
- Archivos de prueba (JPEG con EXIF, orientación, etc.) se generan con Pillow dentro del test; no hay fixtures binarios en el repo.
- Al arreglar un bug, agregar un test que falle sin el arreglo.
- `ResourceWarning` (archivo o conexión SQLite sin cerrar) en un test = **error** (`filterwarnings` en `pyproject.toml`).
- Cobertura actual: `database` 97 %, `services` 92 % (medida en la fase 4).

### Lint y formato
- `ruff check` con reglas E, W, F, B, I, UP (solo se ignora E501: el formateador maneja el largo).
- `ruff format` (estilo Black, línea de 110). Todo el código ya está formateado: una sentencia por línea, sin alineación manual de `=`.
- `mypy` con `check_untyped_defs` y `no_implicit_optional`: **0 errores**. No agregar errores nuevos; usar `X | None` (no `Optional`).

---

## 12. Convenciones

- Comentarios y textos de UI en **español**; identificadores en inglés (`get_photos`, `tag_ids`…), igual que el código existente.
- Type hints en funciones nuevas; modelos como `@dataclass`.
- SQL siempre parametrizado (`?`). Para partes no parametrizables (ORDER BY, PRAGMA) usar allowlist o valores internos controlados.
- Toda operación lenta (I/O masivo, hashing, escaneo) va en un `QThread`, nunca en el hilo de UI.
- Acciones destructivas sobre archivos del disco siempre con `QMessageBox.question` de confirmación.
- Logging en lugar de `print`; nada de `except: pass` silencioso.
- Commits pequeños, un tema por commit, mensaje en español. Correr tests + ruff + mypy antes. Movimientos de código y formato van en commits propios (sin cambios de lógica).
- Constantes (rutas, tamaños, colores) en `config.py`, no repetidas en los módulos.

---

## 13. Deuda técnica y bugs conocidos

El detalle y el orden están en `ROADMAP.md`. Pendientes relevantes:

1. "Eliminar etiqueta" no pasa por la papelera interna (solo se confirma).
2. `get_relocation_plan` hace una consulta por registro para detectar conflictos (1 s para 172k; aceptable, mejorable con un JOIN).
3. `get_photos_for_tagging` carga todas las fotos coincidentes (843 ms para 171k); `QuickTagWindow` podría usar `GalleryModel` como el visor (fase 7).
10. `services.py` pasa de 1.000 líneas: dividirlo por área (galería, etiquetas, mantenimiento) en un commit propio.
11. Aviso de Qt en el log real: `QFont::setPointSize: Point size <= 0 (-1)` (inofensivo; probablemente un estilo con `font-size` en px). Revisar al tocar estilos.
8. Los estilos en línea (`setStyleSheet("color:#4A9EFF;…")`) repiten los hex de la paleta en vez de usar `config.COLORS`; migrarlos al tocar cada diálogo.
9. El `.exe` incluye todo PyQt6 (QML, WebEngine…) por `collect_data_files('PyQt6')` → fase 11 (#83).
4. `QuickTagWindow` carga imágenes en el hilo de UI (con `load_preview_pixmap` ya es rápido, pero un HEIC grande sin caché tarda) → fase 7 (usar `ImageLoadQueue` como el visor).
12. 150 JPEG truncados de la colección real (copias en "Broken pics") no generan miniatura (⚠). Pillow podría leerlos con `ImageFile.LOAD_TRUNCATED_IMAGES`.
13. FFmpeg (QtMultimedia) escribe la información de cada video en la consola en desarrollo; en el `.exe` no hay consola.
5. `SettingsDialog._clear_cache` borra el caché con `shutil.rmtree` en el hilo de UI y sin confirmar (es regenerable, pero con 170k miniaturas tarda).
6. El caché de miniaturas de antes de la fase 1 (formato sin tamaño) queda como huérfano hasta pulsar **Purgar huérfanos** en Configuración.
7. Las fechas guardadas con la regla EXIF vieja se corrigen solo al re-indexar.

---

## 14. Bugs ya resueltos (no reintroducir)

- Tags/categorías borrados reaparecían al reiniciar → flag `seeded` en `app_settings` (test: `test_tag_borrado_no_reaparece`).
- `sqlite3.OperationalError: no such column: md5` → índice creado antes de la migración.
- Fechas falsas a partir de resoluciones (`1920x1080`) → patrones con lookaround (tests en `test_dates.py`).
- `QThread: Destroyed while thread is still running` → `_stop_loader()` + `closeEvent`.
- Conflicto de señal `done` en `QDialog` → renombrada a `done_signal`.
- `ProgrammingError` por ejecutar varias sentencias con `execute` → usar `executescript` (fuera de migraciones) o separar.
- `upsert_photo` no actualizaba `width/height` en re-indexación.
- `subprocess.Popen(["start", ...], shell=True)` solo funcionaba en Windows → `QDesktopServices.openUrl`.
- `build.bat` no encontraba Python → autodetección de rutas.
- Errores invisibles en el .exe (console=False) → logging a archivo + excepthook global.
- "Eliminar faltantes" borraba sin confirmar y con el disco desconectado habría borrado todo → buscar/confirmar/borrar + unidades protegidas (`test_unidad_desconectada_se_protege`).
- Duplicados borrados con `unlink` → `send2trash`.
- `QPixmap` creado en un worker; `wait(500)` que soltaba hilos vivos; señal `finished` redefinida en workers → `QImage` + `retire_thread` + `completed`.
- Miniaturas giradas (orientación EXIF) y de tamaño equivocado (tamaño fuera de la clave) → `exif_transpose` + `THUMB_VERSION` 2.
- Fecha EXIF de modificación en lugar de la de captura → prioridad `DateTimeOriginal`.
- `LIKE 'carpeta%'` incluía `Fotos2` y trataba `_`/`%` como comodines → `folder_like_pattern`.
- Nombres con `&`/`<` rompían el SVG y los `QLabel` → `html.escape` / `PlainText`.
- Tamaño de página no persistía; tags escondidos del sidebar no se podían restaurar; tags no se podían editar.
- Todas las miniaturas de video caían en una sola subcarpeta `v_/` del caché.
- Des-indexar / faltantes / duplicados borraban etiquetas para siempre → papelera interna (`deleted_photos`).
- Cambiar la letra de la unidad obligaba a re-indexar y perder etiquetas → Reubicar.
- El JSON exportado no incluía las asignaciones foto↔etiqueta → formato v2.
- `preview_relocation("")` no fallaba porque `normpath("")` es `"."` → validar antes de normalizar.
- `main.py` de 2.800 líneas → paquete `ui/`.
- Borrar layouts dejaba acumulados los `addStretch()` → `clear_layout`.
- `self.scroll` en diálogos tapaba `QWidget.scroll()` → `self.scroll_box`.
- Un fallo al importar la UI cerraba el `.exe` sin dejar log → imports después de `setup_logging()`.
- Error del cálculo de MD5 dejaba visible la barra de progreso.
- Etiquetado rápido "sin etiquetar" cargaba solo 99.999 de 171.546 fotos (`limit=99_999` fijo) → `limit=-1`.
- Un md5 viejo sobrevivía a cambios del archivo → el upsert lo invalida.
- Paginación inestable con nombres repetidos (sin desempate) → `p.id` al final de cada orden.
- Re-indexar releía todo (14 s por 457 archivos) → incremental por tamaño+mtime.
- Pregenerar miniaturas revisaba el caché de 172k fotos sin poder cancelar → comprobación dentro de los hilos.
- Paginación de 100 en 100 → galería virtualizada (`test_gallery_ui.py`).
- Nombres tipo UUID daban años imposibles (2054…) → año ≤ actual + 1 y fechas compactas inválidas se descartan; se releen al re-indexar (`test_fecha_imposible_se_relee_aunque_el_archivo_no_cambie`).
- Ctrl+A sobre 172k fotos tardaba 1,2 s → `visualRegionForSelection`.
- Workers soltados (`= None`) en su `completed` mientras aún cerraban la conexión → `retire_thread`.
