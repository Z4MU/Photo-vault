# PhotoVault — Guía del proyecto para Claude Code

> Este archivo es el contexto principal del proyecto. Léelo completo antes de tocar código.
> Si algo aquí contradice el código real, **el código manda**: avisa al usuario de la diferencia y actualiza este archivo.
> El plan de trabajo por fases está en **`ROADMAP.md`**. Márcalo al completar cada punto.

---

## 1. Qué es

PhotoVault es una app de escritorio (Windows) para **indexar, navegar, etiquetar y filtrar** una colección personal de fotos y videos muy grande (~172.000 archivos en la DB real, y creciendo). La idea es tener algo rápido y local que no tenga las limitaciones de los gestores genéricos.

- **Usuario / dueño:** Jose. Comunícate en **español**.
- **Entorno:** Windows, VS Code, se ejecuta con `python main.py` desde la terminal integrada.
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
main.py             UI completa (ventana principal + todos los diálogos) + arranque (_startup)
services.py         Lógica de negocio. La UI SOLO habla con esta capa.
database.py         Acceso a SQLite: conexiones, migraciones versionadas, queries
models.py           Dataclasses tipadas + enums de ordenamiento
indexer.py          Escaneo de carpetas, extracción de fecha/dimensiones/duración
thumbnail_cache.py  Caché persistente de miniaturas en disco
backup.py           Backups rotativos de la DB (diario + pre-migración)
xmp_sidecar.py      Leer/escribir etiquetas en archivos .xmp junto a las fotos
logging_setup.py    Logging a archivo + captura global de excepciones
tests/              Suite de pytest (conftest.py aísla DB, backups y caché en tmp)
pyproject.toml      Config de pytest, ruff y mypy
requirements.txt    Dependencias de la app
requirements-dev.txt Dependencias de desarrollo
PhotoVault.spec     Configuración de PyInstaller
build.bat           Script de build para Windows (autodetecta Python)
ROADMAP.md          Plan por fases con casillas
```

### Flujo de capas (respetar siempre)

```
main.py (UI)  →  services.py  →  database.py  →  SQLite
                     ↓                ↓
              thumbnail_cache.py   backup.py
              indexer.py
              xmp_sidecar.py
```

**Reglas:**
- La UI **no** escribe SQL ni llama a `database` directamente. Excepciones existentes y permitidas: `db.init_db()`, `db.close_connection()`, `db.DB_PATH`, `db.SCHEMA_VERSION` y `db.DatabaseTooNewError` (en `_startup`).
- `database.py` devuelve **modelos** (`Photo`, `Tag`, `Stats`…), nunca `sqlite3.Row` hacia afuera.
- La lógica que combina varias queries (p. ej. obtener tags ocultos + contar + paginar) vive en `services.py`.
- ⚠️ Hoy `services.py` todavía tiene SQL directo en `get_indexed_folders` y `purge_cache_orphans` → se mueve a `database.py` en la fase 3.

---

## 4. Modelos (`models.py`)

- `Photo` — `id, path, filename, year, month, media_type ("image"|"video"), duration, filesize, width, height, added_at, md5`. Propiedades: `is_video`, `duration_str` (`M:SS`), `short_name` (truncado a 22 chars). `md5` solo se carga en `get_photo_by_id` y `get_all_photos_for_duplicates`. Las queries usan `_PHOTO_COLUMNS`.
- `Tag` — `id, name, category, color, hidden, sidebar_hidden`.
- `GalleryPage` — `photos, total, offset, limit, sort_field, sort_order` + `page_number`, `total_pages`, `has_prev`, `has_next`.
- `Stats` — `total_photos, total_tags, years, by_month, by_type, top_tags`.
- `DuplicateGroup` — `md5, photos` + `size`, `wasted_bytes`.
- `SortField` (`DATE`, `FILENAME`, `FILESIZE`, `ADDED_AT`) y `SortOrder` (`ASC`, `DESC`).
- `sort_to_sql(field, order)` traduce a SQL usando el **allowlist `_SORT_SQL`**. Nunca interpolar ordenamiento desde input del usuario.

---

## 5. Base de datos (`database.py`)

### Conexiones
- **Una conexión por hilo** con `threading.local()` (`get_connection()`).
- PRAGMAs: `journal_mode=WAL`, `foreign_keys=ON`, `cache_size=-8000`.
- Todo hilo worker (`QThread`) debe llamar `db.close_connection()` al terminar (ya lo hacen `IndexWorker`, `ThumbnailLoader`, `MD5Worker`).
- Escrituras con `with transaction() as conn:` (commit/rollback automático).

### Esquema (versión 2)

```sql
photos(id PK, path UNIQUE, filename, media_type, year, month, filesize,
       width, height, duration, md5, added_at)
tags(id PK, name UNIQUE COLLATE NOCASE, category, color, hidden, sidebar_hidden)
photo_tags(photo_id → photos ON DELETE CASCADE, tag_id → tags ON DELETE CASCADE,
           PK(photo_id, tag_id))
categories(name PK COLLATE NOCASE)
app_settings(key PK, value)      -- flags internos y preferencias
deleted_photos(id PK, batch_id, reason, deleted_at, path,   -- v2: papelera interna
               photo_json, tags_json)
```

Índices: `photos(year)`, `photos(month)`, `photos(md5)`, `photo_tags(photo_id)`, `photo_tags(tag_id)`, `deleted_photos(batch_id)`, `deleted_photos(deleted_at)`.

Migraciones: v1 `_m001_baseline`, v2 `_m002_internal_trash`.
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
`get_photos()` / `get_photo_count()` aceptan: `tag_ids` (AND), `hidden_tag_ids` (exclusión), `search` (LIKE en filename), `folder` (carpeta y subcarpetas), `untagged_only`, más `limit/offset/sort_field/sort_order`.

**Filtro de carpeta:** siempre con `folder_like_pattern(folder)` + `LIKE ? ESCAPE '!'`. Normaliza la ruta, agrega el separador final (`D:\Fotos` no incluye `D:\Fotos2`) y escapa `%`/`_`. El escape es `!` porque `\` es el separador de Windows. Nunca volver a `LIKE folder + '%'`.

### Configuración del usuario
`get_setting(key, default)` / `set_setting(key, value)` sobre `app_settings`. Claves en uso: `seeded`, `page_size`, `xmp_sidecars` (`"1"` = activado).

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

- `index_folder(folder, progress_callback)` recorre recursivamente y hace `upsert_photo` (ON CONFLICT(path) actualiza todo, incluidos `width/height`).
- Devuelve `(nuevas, actualizadas, errores)`; acepta `should_stop()` para cancelar.
- Fecha, en este orden: EXIF (solo imágenes) → nombre del archivo → nombre de la carpeta padre → `mtime`.
  - EXIF con prioridad: `DateTimeOriginal` (IFD Exif) → `DateTimeDigitized` → `DateTime` (IFD0, es la fecha de *modificación*: último recurso). Se usa `img.getexif()` + `get_ifd(0x8769)`, no `_getexif()`.
  - Las fechas ya guardadas con la regla vieja se corrigen al re-indexar la carpeta.
- `DATE_PATTERNS` está compilado y es estricto a propósito (tests en `tests/test_dates.py`):
  - El patrón compacto `YYYYMMDD` usa `(?<!\d)...(?!\d)` para **no** atrapar resoluciones (`1920x1080`, `19201080`).
  - Año válido 1990–2099. Mes fuera de 1–12 → se guarda solo el año.
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
- API: `get_thumbnail(path, size)`, `get_video_thumbnail(path, size)`, `purge_orphans(known_paths)`, `cache_size_mb()`, `CACHE_DIR`.
- `purge_orphans` borra también las miniaturas del formato anterior (sin `_<size>`).

---

## 10. UI (`main.py`)

### Arranque
`__main__` → `setup_logging()` → `QApplication` → `install_qt_handlers()` → `_startup()` (backup diario + `init_db()`; si falla muestra el error y sale con código 1) → `MainWindow`.

### Hilos
Todos heredan de `StoppableThread` (`stop()`, `is_stopping()`):
- `IndexWorker` — indexación; `completed(nuevas, actualizadas, errores)`.
- `ThumbnailLoader` — miniaturas de la página actual; emite `loaded(photo_id, QImage)`.
- `MD5Worker` — hashes faltantes; `completed(n)`.
- `MissingFilesWorker` — busca archivos faltantes; `completed(MissingReport)`. No borra nada.

**Reglas de hilos (causaron crashes `QThread: Destroyed while thread is still running`):**
- Un worker **nunca** crea `QPixmap` ni widgets: emite `QImage`/datos y el `QPixmap` se crea en el slot (hilo de UI).
- **Nunca** redefinir la señal `finished` de `QThread` (tapa la original); para resultados usar `completed`.
- Para descartar un worker que sigue corriendo: `_disconnect_all(señales…)` → `retire_thread(w)`. Pide que pare y guarda la referencia en `_retired_threads` hasta que emita `finished`: no bloquea la UI y Python nunca destruye un hilo vivo. `_stop_loader()` hace esto con el loader de la galería.
- Los diálogos con workers (`DuplicatesDialog`, `IndexDialog`, `DeindexDialog`) sobreescriben `done()` (se llama al cerrar por cualquier vía: botón, Esc, X) para retirar su worker. `IndexDialog` pregunta antes de cancelar.
- `MainWindow.closeEvent` detiene timers, retira el loader y llama `wait_all_threads()`.
- Las funciones lentas de `services`/`indexer` aceptan `should_stop` para que el worker pueda cancelarlas.
- No llamar una señal propia `done` en una subclase de `QDialog` (choca con `QDialog.done`). Por eso `QuickTagWindow` usa `done_signal`.

### Imágenes grandes
`load_preview_pixmap(path, max_side)`: `QImageReader` con `setAutoTransform(True)` (orientación EXIF) y `setScaledSize` (no carga el original completo). Si Qt no puede leer el formato (HEIC), usa la miniatura de Pillow. Usarla en vez de `QPixmap(path)`.

### Texto del usuario en la UI
Nombres de archivo, tags y rutas pueden traer `<`, `&`… En `QLabel` con HTML usar `html.escape()`; en labels de texto plano poner `setTextFormat(Qt.TextFormat.PlainText)` (si no, Qt adivina y puede interpretarlo como HTML). El SVG de estadísticas se arma en `build_bar_chart_svg()`, que escapa.

### Rendimiento de la galería
- Widgets de miniatura se crean en lotes de 10 con un `QTimer(0)` (`_add_next_batch`) para no congelar la UI.
- Paginación (default 100 por página, configurable 10–500; se guarda en `app_settings.page_size` vía `services.get/set_page_size`).
- `resizeEvent` con debounce de 400 ms; solo recarga si cambia el número de columnas.

### Ventana principal (`MainWindow`)
- Sidebar: botones de acciones, botón destacado **⚡ Etiquetado rápido**, filtros por tag agrupados por categoría (colapsables), botón 👁 para esconder tags del sidebar, botón **"👁 Mostrar escondidas (N)"** (solo visible si hay escondidas) que las muestra en cursiva con 🚫 para restaurarlas, estadísticas.
- Barra superior: búsqueda por nombre, ordenamiento (campo + dirección), contador, botón **Seleccionar** (modo selección múltiple) y **Etiquetar selección**.
- Filtro por tags es **AND** (la foto debe tener todos los seleccionados).

### Diálogos
| Clase | Qué hace |
|---|---|
| `PhotoDetailDialog` | Ver foto/video grande, agregar/quitar tags. Videos se abren con `QDesktopServices` (multiplataforma). |
| `BulkTagDialog` | Agregar/quitar un tag a todas las fotos seleccionadas. |
| `QuickTagSetupDialog` | Elegir conjunto a etiquetar: carpeta indexada + tags requeridos + "solo sin etiquetar", con conteo en vivo. |
| `QuickTagWindow` | Etiquetado por teclado: `←/→` navegar, `Space` saltar, `1–9` atajos de tag, `Ctrl+Z` deshacer (historial completo), búsqueda de tags, `Esc` salir. Al terminar la última foto regresa a la galería y esta se recarga. |
| `StatsDialog` | Tarjetas de totales + barras SVG por año y top 10 tags. |
| `DuplicatesDialog` | Calcula MD5 en hilo (cancelable), agrupa duplicados, manda copias a la **Papelera** (con confirmación, nunca la última copia). |
| `SettingsDialog` | Fotos por página, tamaño de caché, limpiar caché, purgar huérfanos. |
| `TagManagerDialog` | Crear/editar (✎ → `EditTagDialog`)/eliminar tags (confirma con el n.º de fotos), flags hidden, exportar/importar JSON. |
| `EditTagDialog` | Cambiar nombre, categoría y color; avisa si el nombre ya existe (`TagNameConflictError`). |
| `CategoryManagerDialog` | Crear/renombrar/eliminar categorías (al eliminar, sus tags pasan a `general`). |
| `IndexDialog` | Indexar carpeta en hilo; muestra nuevas/actualizadas/errores; al cerrar durante la indexación pregunta y cancela. |
| `DeindexDialog` ("🗂 Carpetas") | Lista de carpetas con **Reubicar** y **Eliminar** (a la papelera; confirma con n.º de registros/tags); buscar archivos faltantes (**buscar en hilo → resumen → confirmar → papelera**); botones *Reubicar carpeta o unidad…* y *Papelera de PhotoVault (N)*. |
| `RelocateDialog` | Ruta vieja (combo con las unidades indexadas y si están disponibles) → ruta nueva; vista previa obligatoria con comprobación en disco. |
| `TrashDialog` | Lotes de la papelera interna: restaurar, eliminar lote, vaciar. |
| `SettingsDialog` | (además) opción `.xmp`, *Escribir .xmp ahora*, *Importar desde .xmp*. |

### Archivos faltantes — protección contra pérdida de datos
`services.find_missing_files()` agrupa por unidad (`Path.anchor`) y **no incluye**:
- unidades que no existen (disco desconectado), ni
- unidades donde faltan **todos** los archivos y son ≥ `SUSPICIOUS_ROOT_MIN` (20): probablemente otro disco con la misma letra.

Esas van en `report.skipped` y se muestran al usuario. Si de verdad ya no existen, se quitan con "Eliminar" en su carpeta. `delete_missing(report)` solo borra `report.missing_ids`. Nunca volver a un "buscar y borrar" en un solo paso.

### Estilo visual
- Tema oscuro único definido en la constante `DARK_STYLE` (al final de `main.py`).
- Paleta: fondo `#0D0D1A`, paneles `#13131F` / `#1E1E2E`, bordes `#2D2D3F` / `#3A3A5A`, acento `#4A9EFF`, peligro `#FF4A4A`, advertencia `#FFD700`.
- Mantener esta paleta en cualquier UI nueva.

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

# Verificación (correr SIEMPRE antes de commitear)
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check
.\.venv\Scripts\mypy.exe            # informativo por ahora (ver línea base abajo)

# Generar el .exe (o doble clic en build.bat)
py -m PyInstaller PhotoVault.spec --noconfirm
```

- Salida: `dist\PhotoVault.exe` (un solo archivo, `console=False`, UPX activado, ~148 MB).
- `build.bat` busca Python en: PATH → `py` → `%LOCALAPPDATA%\Python\pythoncore-*` → instalaciones típicas.
- Si el `.exe` abre y se cierra: revisar `~/.photovault/logs/photovault.log`. Si no hay nada, poner `console=True` en el `.spec`, recompilar y ver el traceback.
- `hiddenimports` incluye `PyQt6.QtSvg`, `PyQt6.QtSvgWidgets`, `cv2`, `pillow_heif`, `send2trash.win(.legacy)`. Si agregas un import dinámico nuevo, añádelo ahí.
- `build/`, `dist/`, `.venv/` no se commitean.

### Tests
- `tests/conftest.py` tiene una fixture `autouse` que redirige `database.DB_PATH`, `backup.BACKUP_DIR` y `thumbnail_cache.CACHE_DIR` a `tmp_path`. **Ningún test debe tocar `~/.photovault`.**
- `make_legacy_db(path)` simula una DB de V1 para probar migraciones.
- Para probar contra datos reales: copiar la DB real con la API de backup (abriéndola `?mode=ro`) a un directorio temporal y apuntar `DB_PATH` ahí. Nunca contra la DB real.
- Si tocas `DATE_PATTERNS`: agregar casos válidos y falsos positivos en `tests/test_dates.py`.
- `tests/test_ui_helpers.py` usa `QT_QPA_PLATFORM=offscreen` para probar funciones de `main.py` sin ventanas.
- Archivos de prueba (JPEG con EXIF, orientación, etc.) se generan con Pillow dentro del test; no hay fixtures binarios en el repo.
- Al arreglar un bug, agregar un test que falle sin el arreglo.

### Lint
- `ruff` con reglas E, W, F, B, I, UP. Se ignoran a propósito las reglas del estilo compacto de la UI (`E701/E702`, alineación de `=`) hasta la fase 3.
- **No correr `ruff format`** todavía: reformatearía todo `main.py`. Se hará al dividirlo en la fase 3.
- `mypy`: línea base de **24 errores**, todos de `Optional` implícito (parámetros `= None` tipados como `str`/`list[int]`) y la lista `params` sin anotar en `get_photos`/`get_photo_count`. Se corrigen en la fase 3; no agregar errores nuevos.

---

## 12. Convenciones

- Comentarios y textos de UI en **español**; identificadores en inglés (`get_photos`, `tag_ids`…), igual que el código existente.
- Type hints en funciones nuevas; modelos como `@dataclass`.
- SQL siempre parametrizado (`?`). Para partes no parametrizables (ORDER BY, PRAGMA) usar allowlist o valores internos controlados.
- Toda operación lenta (I/O masivo, hashing, escaneo) va en un `QThread`, nunca en el hilo de UI.
- Acciones destructivas sobre archivos del disco siempre con `QMessageBox.question` de confirmación.
- Logging en lugar de `print`; nada de `except: pass` silencioso.
- Commits pequeños, un tema por commit, mensaje en español. Correr tests + ruff antes.

---

## 13. Deuda técnica y bugs conocidos

El detalle y el orden están en `ROADMAP.md`. Pendientes relevantes:

1. "Eliminar etiqueta" no pasa por la papelera interna (solo se confirma).
2. `get_relocation_plan` hace una consulta por registro para detectar conflictos (1 s para 172k; aceptable, mejorable con un JOIN).
3. Rendimiento: búsqueda sin debounce; commit por archivo al indexar; cada imagen se abre 2 veces (EXIF + tamaño); `get_photos_for_tagging` con `limit=99_999`; `get_indexed_folders` carga todas las rutas en Python → fase 4.
4. `QuickTagWindow` y `PhotoDetailDialog` cargan imágenes en el hilo de UI (con `load_preview_pixmap` ya es rápido, pero un HEIC grande sin caché tarda) → fase 5/7.
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
