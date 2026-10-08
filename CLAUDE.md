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
| **onnxruntime** + **tokenizers** + **numpy** | Búsqueda por contenido (CLIP, fase 10) y hash perceptual. Los modelos **no** van en el `.exe`: se descargan desde Configuración → IA |
| **cryptography** | AES-GCM para la clave del PIN y las miniaturas del contenido oculto (fase 9). PyInstaller lo incluye solo (hook de `pyinstaller-hooks-contrib`) |
| **PyInstaller** | Empaquetado a `PhotoVault.exe` |
| **pytest / ruff / mypy** | Desarrollo (`requirements-dev.txt`, config en `pyproject.toml`) |

Datos del usuario (fuera del repo, nunca commitear):
- `~/.photovault/photovault.db` — base de datos (~33 MB)
- `~/.photovault/thumbs/<2 chars>/<sha1>.jpg` — caché de miniaturas
- `~/.photovault/thumbs_private/<2 chars>/<hmac>.pvt` — miniaturas cifradas de las fotos ocultas (fase 9)
- `~/.photovault/models/clip-b32-multilingual-v1/` — modelo CLIP descargado (~217 MB, fase 10)
- `~/.photovault/embeddings.db` — vector CLIP de cada foto (~1 KB por foto; aparte para no inflar la DB ni los backups; regenerable)
- `~/.photovault/backups/` — copias de seguridad automáticas (§6)
- `~/.photovault/logs/photovault.log` — log rotativo (§7)

---

## 3. Estructura de archivos

```
main.py             Solo el arranque: main() + _startup() (backup, migraciones, papelera)
config.py           Rutas de datos, versión, tamaños de miniatura, paleta (COLORS)
services.py         Lógica de negocio. La UI SOLO habla con esta capa (y con privacy.py).
privacy.py          Contenido oculto: PIN, desbloqueo, código de recuperación, cifrado (capa de servicios)
smart.py            Inteligencia local (capa de servicios): etiquetas sugeridas por carpeta, fotos parecidas
similarity.py       pHash de 64 bits y búsqueda de parecidas (numpy; sin Qt ni DB)
clip_model.py       Modelo CLIP local: descarga verificada (SHA-256, revisión fija), imagen/texto → vector
embedding_store.py  Vectores CLIP en ~/.photovault/embeddings.db (SQLite aparte)
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
  sidebar.py            FolderTreePanel, TimelinePanel, SavedSearchPanel
  tag_panel.py          TagFilterPanel (incluir/excluir, Y/O, buscador, jerarquía)
  filter_bar.py         FilterBar (tipo, años, orientación, resolución, duración, estrellas…)
  gallery_actions.py    GalleryActionsMixin: menú contextual y acciones sobre la selección
  viewer.py             ViewerWindow, ImageView (visor a pantalla completa)
  video_player.py       VideoPlayer (QtMultimedia)
  photo_info.py         InfoPanel (panel de información del visor)
  photo_stage.py        PhotoStage: foto/video ajustado, a resolución de pantalla, con precarga (etiquetado)
  system.py             Explorador, abrir con la app, portapapeles
  main_window.py        MainWindow (sesión, avisos, carpetas vigiladas)
  privacy_actions.py    PrivacyMixin: mostrar/bloquear lo oculto, bloqueo automático, modo pánico
  privacy_guard.py      PrivacyGuard: filtro de eventos de la app (tecla de pánico, inactividad)
  content_search.py     ContentSearchMixin: botón 🧠 del buscador y "Parecidas por contenido"
  background.py         BackgroundTasksMixin: indexar (con cola) y generar miniaturas en segundo plano
  toast.py              ToastManager: avisos breves abajo a la derecha
  welcome.py            EmptyState: bienvenida (colección vacía) y "sin resultados"
  folder_watch.py       FolderWatcher (QFileSystemWatcher + espera de 30 s)
  dialogs/
    photo.py            TagEditor (etiquetas de una foto), BulkTagDialog
    stats.py            StatsDialog
    duplicates.py       DuplicatesDialog
    quick_tag_setup.py  QuickTagSetupDialog (modo, etiqueta, conjunto; recuerda la última)
    quick_tag.py        QuickTagWindow (varias etiquetas)
    review.py           ReviewWindow (sí / no), GridReviewWindow (cuadrícula)
    keymap.py           KeymapDialog + bind_keys / display_keys / normalize_key
    settings.py         SettingsDialog (pestañas General / Miniaturas y .xmp / Privacidad)
    privacy.py          PinDialog, NewPinDialog, RecoveryCodeDialog, ensure_unlocked, PrivacySettingsPanel
    ai_settings.py      AiSettingsPanel (pestaña IA: descargar modelo, analizar, borrar análisis)
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
- `smart.py` también es capa de servicios (importa de `services`; la UI lo usa directo).
- `privacy.py` es parte de la capa de servicios (no tiene SQL; usa `db.get/set_setting` y `thumbnail_cache`); la UI lo usa igual que `services`.
- La UI **no** escribe SQL ni llama a `database` directamente. Excepciones existentes y permitidas: `db.init_db()`, `db.close_connection()` (workers), `db.DB_PATH`, `db.SCHEMA_VERSION` y `db.DatabaseTooNewError` (en `main.py`).
- `database.py` devuelve **modelos** (`Photo`, `Tag`, `Stats`…), nunca `sqlite3.Row` hacia afuera.
- La lógica que combina varias queries (p. ej. obtener tags ocultos + contar + paginar) vive en `services.py`. `services.py` no tiene SQL.
- `main.py` importa arriba **solo** `logging_setup` (que solo importa `config`). Todo lo demás se importa dentro de `main()`/`_startup()`, después de `setup_logging()`, para que un fallo al importar quede en el log (test: `test_startup.py`).

---

## 4. Modelos (`models.py`)

- `Photo` — `id, path, filename, year, month, media_type ("image"|"video"), duration, filesize, width, height, added_at, md5, mtime, rating (0–5), favorite, note, hidden`. `stars` = "★★★☆☆". `hidden` (fase 9) = tiene alguna etiqueta oculta; se calcula al leer (`_HIDDEN_EXPR` en `_PHOTO_COLUMNS`, subconsulta no correlacionada) y decide a qué caché va su miniatura. `mtime` es el del archivo al indexarlo (None = registro de antes de v3 aún no re-indexado). Propiedades: `is_video`, `duration_str` (`M:SS`), `short_name` (truncado a 22 chars). `md5` solo se carga en `get_photo_by_id` y `get_all_photos_for_duplicates`. Las queries usan `_PHOTO_COLUMNS`.
- `Tag` — `id, name, category, color, hidden, sidebar_hidden, parent_id`.
- `SavedSearch` — `id, name, query` (dict de `services.GalleryQuery.to_dict()`).
- `GalleryPage` — `photos, total, offset, limit, sort_field, sort_order` + `page_number`, `total_pages`, `has_prev`, `has_next`.
- `Stats` — `total_photos, total_tags, years, by_month, by_type, top_tags`.
- `DuplicateGroup` — `md5, photos` + `size`, `wasted_bytes`.
- `SortField` (`DATE`, `FILENAME`, `FILESIZE`, `ADDED_AT`, `RATING`) y `SortOrder` (`ASC`, `DESC`).
- `sort_to_sql(field, order)` traduce a SQL usando el **allowlist `_SORT_SQL`**. Nunca interpolar ordenamiento desde input del usuario.
  - Todo orden termina en `p.id` (paginación estable con nombres/tamaños repetidos) y ASC/DESC son exactamente inversos, para que un índice sirva a ambos. Fecha: `year, month, filename, id`.

---

## 5. Base de datos (`database.py`)

### Conexiones
- **Una conexión por hilo** con `threading.local()` (`get_connection()`).
- PRAGMAs: `journal_mode=WAL`, `foreign_keys=ON`, `cache_size=-32000`, `synchronous=NORMAL` (seguro con WAL; mucho más rápido al escribir), `temp_store=MEMORY`.
- Todo hilo worker (`QThread`) debe llamar `db.close_connection()` al terminar (ya lo hacen `IndexWorker`, `ImageLoadQueue`, `MD5Worker`, `TaskWorker`).
- Escrituras con `with transaction() as conn:` (commit/rollback automático).

### Esquema (versión 6)

```sql
photos(id PK, path UNIQUE, filename, media_type, year, month, filesize,
       width, height, duration, md5, added_at,
       mtime REAL,                                   -- v3: indexación incremental
       folder GENERATED ALWAYS AS (rtrim(path, replace(path,'\',''))) VIRTUAL,  -- v3
       rating INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0, note TEXT,  -- v4
       phash INTEGER)                                -- v6: hash perceptual (NULL = sin calcular, 0 = imagen lisa)
tags(id PK, name UNIQUE COLLATE NOCASE, category, color, hidden, sidebar_hidden,
     parent_id → tags ON DELETE SET NULL)                                            -- v4
tag_aliases(alias PK COLLATE NOCASE, tag_id → tags ON DELETE CASCADE)               -- v4
saved_searches(id PK, name UNIQUE COLLATE NOCASE, query_json, created_at)          -- v4
tag_rejections(tag_id → tags ON DELETE CASCADE, photo_id → photos ON DELETE CASCADE,
               rejected_at, PK(tag_id, photo_id))                                  -- v5: los «no»
photo_tags(photo_id → photos ON DELETE CASCADE, tag_id → tags ON DELETE CASCADE,
           PK(photo_id, tag_id))
categories(name PK COLLATE NOCASE)
app_settings(key PK, value)      -- flags internos y preferencias
deleted_photos(id PK, batch_id, reason, deleted_at, path,   -- v2: papelera interna
               photo_json, tags_json)
```

Índices: `photos(year, month, filename)` (`idx_photos_date`), `photos(filename)`, `photos(filesize)`, `photos(added_at)`, `photos(folder)`, `photos(md5)`, `photos(rating, year, month, filename)` (v4), `photos(favorite) WHERE favorite = 1` (parcial, v4), `tag_aliases(tag_id)`, `tag_rejections(photo_id)` (v5), `photo_tags(photo_id)`, `photo_tags(tag_id)`, `deleted_photos(batch_id)`, `deleted_photos(deleted_at)`. (v3 quitó `idx_photos_year`/`idx_photos_month`, cubiertos por el compuesto.)

Migraciones: v1 `_m001_baseline`, v2 `_m002_internal_trash`, v3 `_m003_mtime_and_sort_indexes` (≈1 s sobre la DB real), v4 `_m004_ratings_notes_tag_tree` (0,4 s), v5 `_m005_tag_rejections`, v6 `_m006_perceptual_hash`.

- ⚠️ `COLLATE NOCASE` de SQLite solo ignora mayúsculas **ASCII** ("Mías" ≠ "MÍAS"). Donde importa (nombres de búsquedas guardadas) se compara en Python con `casefold()`.

- `photos.folder` es una columna **calculada** (no se escribe nunca): la carpeta de la foto con la `\` final. `PRAGMA table_info` no la muestra; `_columns()` usa `table_xinfo`.
- El `upsert` pone `md5 = NULL` y `phash = NULL` si cambió el tamaño o el `mtime` (antes un md5 viejo sobrevivía a cambios del archivo).
(La DB real tiene además una columna sobrante `photos.sidebar_hidden` de alguna versión vieja; es inofensiva.)

- `tags.hidden = 1` → las fotos con ese tag **no aparecen** en la galería salvo con el contenido oculto desbloqueado (fase 9, `services._hidden_filter()`). Con PIN y bloqueado tampoco aparece el nombre de la etiqueta (`services._visible_tags()`); sin PIN sí, para poder des-ocultarla como antes.
- `tags.sidebar_hidden = 1` → el tag no aparece en el panel de filtros.
- ⚠️ `ON DELETE CASCADE`: borrar una fila de `photos` borra sus etiquetas. Por eso **todo borrado de fotos pasa por `db.delete_photos(ids, reason)`**, que antes copia registro + etiquetas a `deleted_photos` en la misma transacción. Nunca hacer `DELETE FROM photos` directo (excepción: `apply_relocation` al fusionar, donde las etiquetas ya se pasaron al otro registro).

### Papelera interna
- `delete_photos(ids, reason)`: un lote (`batch_id` uuid) por operación; `reason` es texto para el usuario ("Carpeta des-indexada: …").
- La papelera guarda también `rating`, `favorite` y `note` (`_TRASH_PHOTO_FIELDS`).
- `restore_trash_batch(batch_id)` → `(restaurados, fusionados)`: si la ruta ya volvió a indexarse, le suma las etiquetas (y valoración/nota con `_merge_extras`); los tags borrados se recrean con la categoría/color **del momento del borrado**.
- `services.purge_old_trash()` corre en `_startup()` y elimina lo que tenga más de `TRASH_KEEP_DAYS` (30) días.
- No guarda tags borrados con "Eliminar etiqueta" (eso se confirma mostrando cuántas fotos la tienen).

### Reubicar (cambiar prefijo de ruta)
- `get_relocation_plan(old, new)` → `[(photo_id, ruta_nueva, id_existente)]`; `apply_relocation(plan)` → `(movidos, fusionados)`.
- `services.preview_relocation()` revisa en disco una muestra de 25 rutas nuevas (`looks_right` si existen ≥ 50 %) para detectar errores de tipeo. La UI exige vista previa antes de aplicar y pide confirmación extra si `looks_right` es falso.
- Si la ruta nueva ya estaba indexada (se re-indexó), se fusionan: etiquetas, valoración más alta, favorita y nota al registro existente, y el viejo se quita.

### Filtros de consulta
`PhotoFilter` (fase 6) agrega: `tag_groups` (Y de grupos; cada grupo = etiqueta + descendientes), `any_tag_ids` (O), `exclude_tag_ids` (NO), `media_type`, `year_from/to`, `min_pixels`, `orientation` (con 2 % de tolerancia para "cuadrada"), `min/max_duration`, `min_rating`, `favorites_only`, `has_note`, `not_rejected_for`/`rejected_for` (v5, revisión); `search` busca en el nombre **o en la nota**. La galería usa `query_photos(filtro, orden, limit, offset)` y `count_filtered(filtro)`.

Para la galería continua: `get_photo_ids(filtro, orden, limit, offset)` (solo ids, mismo orden que `get_photos`), `count_before_date(filtro, año, mes, orden)` (fila a la que saltar; `mes=None` = el año, `NO_MONTH` = las de ese año sin mes; respeta dónde pone SQLite los NULL en ASC/DESC) y `get_date_histogram(filtro)`.

`get_photos()` / `get_photo_count()` aceptan: `tag_ids` (AND), `hidden_tag_ids` (exclusión), `search` (LIKE en filename), `folder` (carpeta y subcarpetas), `untagged_only`, más `limit/offset/sort_field/sort_order`. Ambas arman el WHERE con **`_build_where(PhotoFilter(...))`**: un filtro nuevo se agrega ahí (una sola vez) y en `PhotoFilter`.

**Filtro de carpeta:** siempre con `folder_like_pattern(folder)` + `LIKE ? ESCAPE '!'`. Normaliza la ruta, agrega el separador final (`D:\Fotos` no incluye `D:\Fotos2`) y escapa `%`/`_`. El escape es `!` porque `\` es el separador de Windows. Nunca volver a `LIKE folder + '%'`.

### Etiquetas: jerarquía, alias, fusión (v4)
- `update_tag(id, nombre, categoría, color, parent_id=_KEEP, aliases=None)`: todo en **una** transacción; un padre que formaría un ciclo o un alias que choca con otro nombre/alias lanza y no cambia nada.
- `resolve_tag_name(nombre)` busca nombre **o alias**: `services.add_tag`/`bulk_add_tag`/importar usan `_tag_id_for_name` (escribir un alias agrega la etiqueta real).
- `merge_tags(origen, destino)`: fotos, hijas y alias pasan al destino y el nombre viejo queda como alias. Si el destino descendía del origen, sube al lugar del origen (si no, habría un ciclo). `services.merge_tags` además corrige las búsquedas guardadas que usaban el origen.
- La expansión padre → descendientes se hace en `services.GalleryQuery.photo_filter()` (`tag_descendants()`). `tag_path_names()` = `{id: [raíz, …, nombre]}`.
- Valoración/favoritas/notas: `set_rating(ids, n)`, `set_favorite(ids, bool)`, `set_note(id, texto)`; para exportar/importar `get_photo_extras()` / `merge_photo_extras()` (solo agrega: la valoración más alta, la nota si no había).

### Revisión por etiqueta (v5)
- Solo se guardan los **«no»** (`tag_rejections`); un «sí» es la etiqueta misma. «Revisada para X» = tiene X (o una hija) **o** tiene un «no» para X.
- `add_rejections`, `remove_rejections`, `get_rejected_ids`, `get_ids_with_tag`, `get_all_rejections` (exportar).
- Los «no» se borran solos (CASCADE) al borrar la etiqueta o la foto; **no** pasan por la papelera interna ni se trasladan al fusionar etiquetas (un «no es playa» no dice nada de la otra).

### Configuración del usuario
`get_setting(key, default)` / `set_setting(key, value)` sobre `app_settings`. Claves en uso: `seeded`, `thumb_size` (slider de la galería, 100–400), `xmp_sidecars` (`"1"` = activado), `quick_tag_keymap`, `quick_tag_tag_keys`, `quick_tag_setup`, `quick_tag_resume`, `session_state`, `restore_session`, `watched_folders`, `watch_on_start`, `watch_live`, `privacy_vault` (clave maestra cifrada), `privacy_attempts`, `privacy_options` (JSON; si están rotos se usa lo de fábrica). (`page_size` quedó sin uso desde la fase 5.)

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
- **Dimensiones** con la rotación EXIF aplicada (orientación 5–8 intercambia ancho y alto): así se ven las fotos de celular. Lo indexado antes de la fase 6 se corrige con **Releer todos los archivos** (`IndexDialog` → `index_folder(force=True)`).
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
- **Fotos ocultas (fase 9):** `get_photo_thumbnail(photo)` con `photo.hidden` nunca escribe en `CACHE_DIR`: con el contenido desbloqueado (`privacy` llama `set_private_codec`) la guarda cifrada en `PRIVATE_DIR` (`<hmac>.pvt`, nonce + AES-GCM); bloqueado devuelve None sin generar nada. `remove_plain(fotos)` borra las sin cifrar de los 3 tamaños; `purge_private_orphans` necesita estar desbloqueado. `services.secure_hidden_thumbnails()` corre al ocultar una etiqueta, tras cada cambio de etiquetas (`_tags_changed`, que también sincroniza los .xmp) y al abrir la ventana (en un hilo).

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
- Toda ventana con una cola propia llama además `retire_on_destroy(self, cola)` (`weakref.finalize`): si se descarta sin pasar por `done()` (p. ej. nunca se mostró), el hilo se retira en vez de destruirse vivo (eso aborta la app). No usar la señal `destroyed` para esto: corre en medio de la destrucción en C++.
- `retire_thread` tolera un hilo que Qt ya destruyó (`RuntimeError`).
- No conectar señales de controles antes de terminar de construir la ventana: un `setChecked` inicial dispara el slot con atributos que aún no existen, y una excepción en un slot **aborta** la app (código 0xC0000409) si no hay excepthook (tests).

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

### Sidebar (`ui/sidebar.py`, `ui/tag_panel.py`)
Pestañas **🏷 Etiq.** (`TagFilterPanel`: cada etiqueta es un `TagFilterButton` que alterna nada → incluir ✓ → excluir ✕; combo "todas (Y) / alguna (O)"; buscador por nombre o alias; contador de fotos; las hijas van debajo del padre con sangría), **⭐ Álbum** (`SavedSearchPanel`: `services.BUILTIN_ALBUMS` + búsquedas guardadas, con contadores calculados solo con la pestaña visible; clic derecho renombra/elimina), **Carpetas** (`FolderTreePanel`, árbol de `services.get_folder_tree()`; un clic filtra y aparece un chip 📁 ✕ arriba) y **Fechas** (`TimelinePanel`, `services.get_date_histogram` con los filtros actuales; se recalcula solo si la pestaña está visible). Clic en un año/mes → `go_to_date` (cambia a orden por fecha si hace falta y selecciona la primera foto).

### Filtros, búsquedas guardadas, estrellas (fase 6)
- `services.GalleryQuery` lleva **todo** lo que define la galería (etiquetas incluir/excluir/O, texto, carpeta, orden y los atributos de `FilterBar`); `to_dict`/`from_dict` la guardan como JSON (ignora claves desconocidas: las búsquedas viejas siguen abriendo). `attribute_filter_count()` es el número del botón "⚙ Filtros (n)".
- `MainWindow.apply_gallery_query(q)` pone cada control según una consulta (búsqueda guardada); los controles se actualizan sin emitir señales y se recarga una sola vez.
- `FilterBar.set_values(q)` no emite `changed`; duración sin tipo implica "videos".
- Acciones sobre la selección en `GalleryActionsMixin` (`ui/gallery_actions.py`): teclas **1–5 / 0** (estrellas), **F** (favorita), menú contextual con submenú de valoración. Más de `CONFIRM_OVER` (500) fotos pide confirmación. Después se llama `reload_keep_position()`.

### Visor (`ui/viewer.py`)
- `ViewerWindow(source, fila)` recorre cualquier `PhotoSequence` (`count()` + `photo_at()`; `GalleryModel` lo cumple, `ListSequence` para listas). Pantalla completa por defecto; al cerrar, la galería selecciona `current_row` y recarga si `tags_were_changed`.
- Teclas propias de la fase 6: **1–5 / 0** estrellas, **F** favorita, **Z** alterna ajustar/100 %, **F11** pantalla completa. Cambian el mismo objeto `Photo` que tiene la galería en memoria (se ve sin recargar) y marcan `tags_were_changed`.
- `InfoPanel` tiene la **nota** (`QPlainTextEdit`): se guarda 800 ms después de dejar de escribir, al cambiar de foto y al cerrar (`shutdown`).
- Fotos: `ImageView` (`QGraphicsView`) con zoom (rueda, +/−, Z), arrastre y rotación de **solo la vista**. Decodifica hasta `config.VIEWER_MAX_SIDE` (6000) en su propia `ImageLoadQueue` con `load_full_image` (Pillow si Qt no puede: HEIC). Mientras carga muestra la miniatura de `QPixmapCache`; guarda 3 imágenes y precarga la anterior y la siguiente.
- GIF/WebP animados con `QMovie` (`is_animated`). Videos con `VideoPlayer` (QtMultimedia, backend FFmpeg del wheel de PyQt6; si falla, botón "Abrir con…").
- `InfoPanel` (tecla I): datos de la DB, EXIF de cámara leído en un `TaskWorker` (`services.get_photo_details` → `indexer.read_exif_details`) y `TagEditor`. Si la DB no tiene la resolución (HEIC viejos) usa la de la imagen cargada.
- Atajos en `SHORTCUTS_HELP` (F1 los muestra junto con los de la galería). Son `QShortcut` del diálogo: un `QLineEdit` con foco (agregar etiqueta) se queda con las letras y flechas.
- Los botones de la barra y el `ImageView` son `NoFocus`: si no, las flechas moverían el scroll en vez de cambiar de foto.

### Tareas en segundo plano (`ui/background.py`, `BackgroundTasksMixin`)
- Una a la vez (`_bg_worker`, `_bg_kind` = `"index"` | `"thumbs"`), con progreso y **Cancelar** en la barra de estado (`TaskStatusWidget`). Se puede seguir usando la app.
- `start_indexing(folder, force=False, auto=False)`: si ya hay una indexación, la pedida **espera en `_index_queue`** (sin repetir carpetas). `auto` = carpeta vigilada: sin aviso si no hubo cambios y sin recargar la galería. Las miniaturas de lo nuevo se generan cuando la cola se vacía. La indexación tiene prioridad: cancela una generación de miniaturas en curso; Cancelar también vacía la cola.
- `IndexWorker` emite progreso como mucho cada `PROGRESS_INTERVAL` (0,1 s): una señal por archivo saturaba la UI (23 s vs 0,8 s).
- **`_closing`**: `closeEvent` lo pone en True y desde ahí la ventana ignora avisos tardíos (`completed`/`error` que ya estaban en camino) y no empieza tareas nuevas.
- `start_thumbnail_generation(None)` = toda la colección (botón *Generar todas* en Configuración); devuelve False si hay otra tarea.
- Al cerrar con una indexación en curso, pregunta; lo ya indexado se conserva.

### Ventana principal (`MainWindow`)
- Sidebar: botones de acciones, botón destacado **⚡ Etiquetado rápido**, filtros por tag agrupados por categoría (colapsables), botón 👁 para esconder tags del sidebar, botón **"👁 Mostrar escondidas (N)"** (solo visible si hay escondidas) que las muestra en cursiva con 🚫 para restaurarlas, estadísticas.
- Barra superior: búsqueda por nombre, ordenamiento (campo + dirección), contador, botón **Seleccionar** (modo selección múltiple) y **Etiquetar selección**.
- Filtro por tags es **AND** (la foto debe tener todos los seleccionados).

### Sesión, avisos, bienvenida, carpetas vigiladas (fase 8)
- **Sesión:** `closeEvent` guarda `session_state()` (geometría en base64, `GalleryQuery.to_dict()`, pestaña, barra de filtros, primera fila visible); al abrir, `_restore_geometry()` y `_initial_load()` usan `services.restorable_query()` (None si es la de por defecto o ya no muestra nada → se abre con todo y un aviso). Opción "Recordar…" en Configuración (`restore_session`).
- **Avisos:** `self.toast(texto, kind, ms)` (`info`/`success`/`warning`/`error`). Para confirmaciones que no requieren acción; los errores que sí la requieren siguen siendo `QMessageBox`. Texto plano (nombres de archivo). `BulkTagDialog.summary` → aviso.
- **Galería vacía:** `gallery_stack` alterna `view` / `EmptyState` en `_after_model_reset` (bienvenida si `get_totals()[0] == 0`).
- **Carpetas vigiladas:** `services.get/set_watched_folders`, `available_watched_folders()` (solo las que existen) y `watch_dirs(raíces)` (raíz + subcarpetas indexadas, desde la DB, máx. `MAX_WATCHED_DIRS`). `apply_watch_settings()` comprueba qué existe **en un `TaskWorker`** (un USB dormido tarda ~9 s en responder), monta `FolderWatcher` y, al abrir, revisa con `scan_watched_folders` 3 s después. `FolderWatcher.changed(raíz)` se emite 30 s (`SETTLE_MS`) después del último cambio → `start_indexing(raíz, auto=True)`; tras cada revisión automática se actualizan las carpetas vigiladas (subcarpetas nuevas).

### Privacidad (fase 9)
- **Modelo:** `privacy.set_pin` crea una clave maestra aleatoria y la guarda cifrada (AES-GCM) con el PIN (scrypt, `KDF_N`) y con un **código de recuperación** (20 caracteres, se muestra una vez). Desbloquear = descifrarla; queda solo en memoria hasta `lock()`. Cambiar el PIN solo la vuelve a cifrar. Tras `MAX_ATTEMPTS` (5) errores hay que esperar `LOCKOUT_SECONDS` × tandas (`LockedOutError`); se guarda en `privacy_attempts` (reiniciar no lo salta).
- `privacy.show_hidden_content()` (= desbloqueado) decide si la galería incluye lo oculto; `hidden_locked()` (= hay PIN y está bloqueado) decide si se esconden también los nombres. Sin PIN no hay forma de desbloquear.
- **UI:** `PrivacyMixin` (`ui/privacy_actions.py`): `toggle_hidden_content()` (Ctrl+Shift+H y el botón 🔒 de la barra de estado), `lock_hidden_content()` (cierra el visor y todo diálogo abierto: pueden estar mostrando lo oculto), `privacy_changed()` (vacía `QPixmapCache` y recarga), `panic()`. `PrivacyGuard` es un filtro de eventos **de la app** (así la tecla funciona en diálogos modales): acepta el `ShortcutOverride` de la tecla de pánico para que ningún atajo se la quede y actúa en el `KeyPress`, siempre fuera del filtro (`QTimer.singleShot`).
- `ensure_unlocked(parent)` pide el PIN (o lo crea). En tests hay que reemplazarlo (`monkeypatch`): el diálogo real espera para siempre.
- Exportar etiquetas con lo oculto bloqueado pide el PIN (el archivo lo incluye). Los `.xmp` no: ya se advierte que exponen los nombres.
- ⚠ La DB **no** está cifrada: protege de miradas, no de alguien con acceso al archivo. La UI lo dice.

### Inteligencia local (fase 10)
- **Etiquetas sugeridas** (`smart.suggest_tags(ids)`): por la parte de la carpeta que tiene cada etiqueta (≥ `MIN_SHARE` 20 % y ≥ 2 fotos; la carpeta de arriba pesa `PARENT_WEIGHT` 0,5) y por el nombre de la carpeta (palabras completas, sin acentos: «Cancún» → «cancun»; 0,9). No sugiere las que ya tienen todas (`db.get_common_tag_ids`) ni las ocultas bloqueadas. Se muestran en `TagEditor` (visor; un clic la agrega) y en `BulkTagDialog` (un clic la elige). ~3 ms por foto con la DB real.
- **Parecidas** (`smart.find_similar_photos(sensibilidad)`, en un `TaskWorker` desde `DuplicatesDialog` → *Parecidas*): calcula el pHash que falte desde la miniatura de 200 px (si falta, la genera: la primera vez lee los originales) y agrupa con `similarity.find_similar` (bandas + palomar, 1,5 s para 174k). Sensibilidad = bits distintos: estricta 3, normal 6, amplia 8. No repite grupos de idénticas (mismo md5) ni incluye lo oculto bloqueado. `DuplicateGroup.similar` / `.best` (★ mejor calidad = más píxeles).
- **Búsqueda por contenido (#55):** `clip_model` (CLIP ViT-B/32 de OpenAI para imagen + `clip-ViT-B-32-multilingual-v1` para texto: busca en español). `smart.analyze_photos` (tarea `"content"` de `BackgroundTasksMixin`, cancelable e incremental por `mtime`): lee el original reducido con `draft` (videos: su miniatura de 480), procesa en tandas de 16 mientras los hilos leen la siguiente; un archivo ilegible queda con vector nulo (no se reintenta). 37 ms por foto con la colección real (≈ 1,8 h para 172k la primera vez); buscar entre 172k: ~0,25 s, 176 MB en memoria. Tras indexar fotos nuevas se analizan solas (`ai_auto_analyze`).
- `GalleryQuery.semantic`: texto, `@foto:<id>`, `@etiqueta:<id>` o `@adulto` → `smart.semantic_ranking()` (ids de más a menos parecido, con caché por versión del almacén) → `PhotoFilter.only_ids` (un parámetro JSON con `json_each`) y `services.get_gallery_ids/chunk` reordenan por parecido (el orden elegido no se usa; los combos se deshabilitan). Así funciona con todos los filtros, búsquedas guardadas, la sesión y el etiquetado rápido.
- Umbrales medidos con 3.000 fotos reales: texto-foto está muy comprimido (mediana ~0,22, relevante ~0,28) → aparece lo que supera media + `TEXT_MIN_Z` (2,5) desviaciones, hasta 1.000; foto-foto ≥ 0,83 (el 99 % de los pares al azar < 0,81).
- **Etiquetas por contenido (#56):** con ≥ `TAG_MIN_EXAMPLES` (5) fotos analizadas, cada etiqueta tiene su promedio y su propio umbral (percentil 25 del parecido de sus fotos) → `content_tag_suggestions` en el visor, junto a las de carpeta. En el etiquetado rápido, **Orden: 🧠 primero las más parecidas a la etiqueta** (`@etiqueta:<id>`) o **las que parecen contenido adulto** (`@adulto`: prompts adulto − normal, sin corte: el límite no es claro con dibujos, se revisa de la más probable a la menos). Para ocultarlas: etiqueta + PIN (fase 9).
- Tests: `FakeClip` (color → vector); `PV_CLIP_MODELS=<carpeta con clip-b32-multilingual-v1>` activa el test con el modelo real. `conftest` redirige `embedding_store.DB_PATH` y `clip_model.MODELS_DIR` y limpia los cachés de `smart`.
- Lecciones con la colección real: dHash encadenaba capturas de pantalla y fondos lisos → pHash (DCT) + las imágenes casi lisas no se comparan (`FLAT`) + grupos alrededor de un centro (sin cadenas A~B~C).
- `clear_layout` ahora vacía también los layouts anidados (filas de botones).

### Diálogos
| Clase | Qué hace |
|---|---|
| `ViewerWindow` | Visor (ver arriba). Reemplaza al antiguo `PhotoDetailDialog`. |
| `BulkTagDialog` | Agregar/quitar un tag a todas las fotos seleccionadas. |
| `QuickTagSetupDialog` | Modo (sí / no, cuadrícula, varias etiquetas), etiqueta a revisar, conjunto (colección, galería actual, álbum, búsqueda guardada) + carpeta + "solo sin etiquetar" + "volver a revisar las respondidas"; cuenta lo pendiente en vivo; botón ⌨ Teclas…; recuerda la última configuración. |
| `ReviewWindow` | Sí / no: una foto a la vez (`PhotoStage`), destello verde/rojo al responder, Ctrl+Z vuelve a la foto. |
| `GridReviewWindow` | Cuadrícula de 9/12/16/20 (`thumbnail_queue`, 480 px, precarga la página siguiente): 1–9 / clic marcan, Enter confirma (resto = «no»), Ctrl+Z vuelve a la página con sus marcas. 1–9 y flechas son fijas. |
| `QuickTagWindow` | Varias etiquetas: cada etiqueta con su tecla (cualquiera, `services.get_tag_keys`), Ctrl+R repite las de la anterior, Ctrl+Z (por acción) vuelve a la foto, buscador por nombre o alias; recuerda la última foto por consulta y ofrece continuar. |
| `KeymapDialog` | Remapear teclas (2 por acción) y la tecla de cada etiqueta; no deja guardar choques (`services.keymap_conflicts`). |
| `StatsDialog` | Tarjetas de totales + barras SVG por año y top 10 tags. |
| `DuplicatesDialog` | *Idénticas*: calcula MD5 en hilo (cancelable) **solo de archivos cuyo tamaño se repite** (38 % de la colección real). *Parecidas* (fase 10): hash perceptual con sensibilidad. Agrupa, marca la de mejor calidad y manda copias a la **Papelera** (con confirmación, nunca la última copia). Muestra los 200 grupos más grandes y 10 fotos por grupo. |
| `SettingsDialog` | Pestañas: **General** (recordar sesión, carpetas vigiladas), **Miniaturas y .xmp** (tamaño, limpiar —también el privado—, purgar huérfanos, generar todas, .xmp) y **🔒 Privacidad** (`PrivacySettingsPanel`: crear/cambiar/quitar PIN y código nuevo —al momento—, minutos para bloquear, bloquear al minimizar, tecla y acción del modo pánico, validada con `privacy.panic_key_problem`). |
| `PinDialog` / `NewPinDialog` / `RecoveryCodeDialog` | Pedir el PIN (cuenta regresiva si hay que esperar; "¿Olvidaste el PIN?" → código de recuperación → PIN nuevo), crear uno, mostrar el código (no se cierra con "Listo" hasta marcar que se guardó). |
| `TagManagerDialog` | Crear/editar (✎ → `EditTagDialog`)/**fusionar** (⇢)/eliminar tags (confirma con el n.º de fotos), buscador, árbol con contadores y alias, flags hidden, exportar/importar JSON. |
| `EditTagDialog` | Cambiar nombre, categoría, color, **etiqueta padre** (sin ciclos: no ofrece descendientes) y **alias**; avisa si el nombre o un alias ya existe (`TagNameConflictError`). |
| `CategoryManagerDialog` | Crear/renombrar/eliminar categorías (al eliminar, sus tags pasan a `general`). |
| `IndexDialog` | Solo elige la carpeta y emite `start_requested(folder, releer_todo)`; la indexación corre en `MainWindow` (segundo plano). Con `busy=True` deshabilita el botón. |
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

### Etiquetado rápido (fase 7)
- Tres modos que comparten piezas: `services.PhotoList(ids)` (lista fija leída por tramos de 300; cumple `PhotoSequence`), `PhotoStage` (precarga las 3 siguientes a resolución de pantalla en su `ImageLoadQueue`; videos en silencio) y las teclas de `services.get_keymap()` (`KEY_ACTIONS`: acción → descripción y teclas de fábrica, prefijo `review.` / `grid.` / `multi.` / `common.`).
- **Sí / no y cuadrícula** recorren `services.review_query(base, tag)` = `base` + excluir la etiqueta (con hijas) + `not_rejected_for`; por eso retomar es automático. `review_answer(tag, sí_ids, no_ids)` devuelve el `ReviewState` previo de cada foto y `review_restore` lo deshace. Un «no» sobre una foto que tenía la etiqueta (re-revisión) se la quita.
- Las teclas se enlazan con `bind_keys` (un `QShortcut` por tecla, contexto de ventana). Un atajo de ventana solo funciona en la ventana **activa**: en tests, `show()` + `activateWindow()` y esperar a que lo sea.
- Los conteos de `QuickTagSetupDialog` usan `review_stats(base, tag)` → `total / tagged / rejected / pending`.

### Formato de exportación de tags (JSON, versión 3)
```json
{ "version": 2, "app": "PhotoVault", "exported_at": "2026-10-04T12:00:00",
  "tags": [{"name": "...", "category": "...", "color": "#RRGGBB", "hidden": false, "sidebar_hidden": false,
            "parent": "nombre del padre", "aliases": ["..."]}],
  "categories": ["..."],
  "assignments": [{"path": "G:\\...\\a.jpg", "filename": "a.jpg", "filesize": 123, "tags": ["x", "y"],
                   "rating": 4, "favorite": true, "note": "...", "rejected": ["etiquetas con «no»"]}] }
```
- Se siguen aceptando las versiones 1 (sin `assignments`) y 2 (sin padre/alias/valoración). `parent`, `aliases`, `rating`, `favorite` y `note` son opcionales; `assignments` incluye también fotos sin etiquetas pero con valoración, favorita o nota.
- Importar padre/alias solo si la etiqueta no tenía; valoración/favorita/nota solo se agregan.
- Las búsquedas guardadas **no** se exportan (usan ids de etiquetas de esta DB).
- `rejected` (fase 7): al importar, un «no» se agrega solo si la foto no tiene esa etiqueta.
- La importación solo crea tags que no existan (nunca sobrescribe) y **solo agrega** asignaciones, nunca quita.
- Emparejamiento de fotos: ruta exacta → si no, `(nombre, tamaño)` cuando hay **una sola** coincidencia (cambio de unidad/carpeta). Si hay varias, no adivina (`photos_missing`).
- La UI pregunta si importar las asignaciones (Sí / No / Cancelar).

### Sidecars XMP (`xmp_sidecar.py`)
- Opcional (Configuración, desactivado por defecto). Con la opción activa, `services._sync_sidecars(ids)` se llama tras **cada** función que cambia etiquetas (`add_tag`, `add_tag_by_id`, `remove_tag`, `bulk_*`, `update_tag`, `delete_tag`, `rename/delete_category`, `import_tags`). Si agregas otra función que cambie etiquetas, llama `_sync_sidecars` también.
- Escribe `<foto>.<ext>.xmp` (digiKam/darktable); lee también `<foto>.xmp` (Lightroom). `dc:subject` + `lr:hierarchicalSubject` (`categoria|padre|tag`) + `xmp:Rating` (atributo; al leer se acepta también como elemento). Un sidecar con solo valoración también se escribe; `set_rating` sincroniza.
- Al comparar "sin cambios" la categoría se reduce al primer nivel (así se lee): si no, cada foto con padre se reescribiría siempre.
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
- `tests/conftest.py` también tiene fixtures `autouse` que hacen fallar el test (con el mensaje) si aparece un `QMessageBox` crítico/advertencia/pregunta que el test no reemplazó (`information` se acepta solo) o si hay una excepción en un slot de Qt. Antes: el test se colgaba esperando el diálogo, o PyQt abortaba el proceso sin decir dónde. `PV_NO_SLOT_HOOK=1` desactiva lo segundo.
- `tests/conftest.py` tiene una fixture `autouse` que redirige `database.DB_PATH`, `backup.BACKUP_DIR`, `thumbnail_cache.CACHE_DIR` y `thumbnail_cache.PRIVATE_DIR` a `tmp_path` (además bloquea `privacy` antes y después de cada test y baja `privacy.KDF_N` para que scrypt sea rápido) y, al terminar cada test, espera los hilos retirados (`wait_all_threads`; si el proceso termina con uno vivo, Qt aborta). También define `qapp` (sesión) para los tests de UI. **Ningún test debe tocar `~/.photovault`.**
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
10. `services.py` (~1.700 líneas), `database.py` (~1.800) y `ui/main_window.py` (~810) son demasiado grandes: dividirlos por área (galería, etiquetas, mantenimiento) en commits propios (solo mover código).
11. Aviso de Qt en el log real: `QFont::setPointSize: Point size <= 0 (-1)` (inofensivo; probablemente un estilo con `font-size` en px). Revisar al tocar estilos.
8. Los estilos en línea (`setStyleSheet("color:#4A9EFF;…")`) repiten los hex de la paleta en vez de usar `config.COLORS`; migrarlos al tocar cada diálogo.
9. El `.exe` incluye todo PyQt6 (QML, WebEngine…) por `collect_data_files('PyQt6')` → fase 11 (#83).
12. 150 JPEG truncados de la colección real (copias en "Broken pics") no generan miniatura (⚠). Pillow podría leerlos con `ImageFile.LOAD_TRUNCATED_IMAGES`.
13. FFmpeg (QtMultimedia) escribe la información de cada video en la consola en desarrollo; en el `.exe` no hay consola.
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
- Ancho/alto sin la rotación EXIF (fotos verticales de celular como horizontales) → se intercambian al indexar (`test_dimensiones_con_la_orientacion_aplicada`).
- "Mías" y "MÍAS" eran búsquedas distintas (`NOCASE` solo ASCII) → `casefold()`.
- Etiquetado rápido cargaba todas las fotos (843 ms) y decodificaba en el hilo de UI → `services.PhotoList` (por tramos) + `PhotoStage` (hilo, precarga).
- Una ventana descartada sin mostrarse dejaba su hilo vivo y Qt abortaba → `retire_on_destroy`.
- `QuickTagSetupDialog` llamaba `_refresh` a medio construir (señales conectadas antes de tiempo) → se conectan al final (`test_configuracion_se_construye_sin_errores_en_slots`).
- Un "indexación terminada" que llegaba con la ventana ya cerrada arrancaba miniaturas y mostraba un error → `_closing` (`test_ventana_cerrada_ignora_avisos_tardios`).
- Una señal de progreso por archivo al indexar saturaba la UI → `PROGRESS_INTERVAL`.
- Abrir con una carpeta vigilada en un USB dormido congelaba la ventana 9 s → comprobación en un hilo.
