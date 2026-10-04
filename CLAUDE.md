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
| **pillow-heif** (opcional) | Soporte `.heic` / `.heif`. **Hoy NO está instalado**: los HEIC se indexan sin miniatura (no crashea, queda un warning en el log) |
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
```

**Reglas:**
- La UI **no** escribe SQL ni llama a `database` directamente. Excepciones existentes y permitidas: `db.init_db()`, `db.close_connection()`, `db.DB_PATH`, `db.SCHEMA_VERSION` y `db.DatabaseTooNewError` (en `_startup`).
- `database.py` devuelve **modelos** (`Photo`, `Tag`, `Stats`…), nunca `sqlite3.Row` hacia afuera.
- La lógica que combina varias queries (p. ej. obtener tags ocultos + contar + paginar) vive en `services.py`.
- ⚠️ Hoy `services.py` todavía tiene SQL directo en `delete_photo_file`, `remove_missing_files`, `deindex_folder`, `get_indexed_folders`, `purge_cache_orphans` → se mueve a `database.py` en la fase 3.

---

## 4. Modelos (`models.py`)

- `Photo` — `id, path, filename, year, month, media_type ("image"|"video"), duration, filesize, width, height, added_at`. Propiedades: `is_video`, `duration_str` (`M:SS`), `short_name` (truncado a 22 chars). ⚠️ `_row_to_photo` no llena `width/height/added_at`.
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

### Esquema (versión 1)

```sql
photos(id PK, path UNIQUE, filename, media_type, year, month, filesize,
       width, height, duration, md5, added_at)
tags(id PK, name UNIQUE COLLATE NOCASE, category, color, hidden, sidebar_hidden)
photo_tags(photo_id → photos ON DELETE CASCADE, tag_id → tags ON DELETE CASCADE,
           PK(photo_id, tag_id))
categories(name PK COLLATE NOCASE)
app_settings(key PK, value)      -- flags internos, p. ej. 'seeded'
```

Índices: `photos(year)`, `photos(month)`, `photos(md5)`, `photo_tags(photo_id)`, `photo_tags(tag_id)`.
(La DB real tiene además una columna sobrante `photos.sidebar_hidden` de alguna versión vieja; es inofensiva.)

- `tags.hidden = 1` → las fotos con ese tag **no aparecen** en la galería.
- `tags.sidebar_hidden = 1` → el tag no aparece en el panel de filtros.
- ⚠️ `ON DELETE CASCADE`: borrar/des-indexar una foto **borra sus etiquetas para siempre**.

### Filtros de consulta
`get_photos()` / `get_photo_count()` aceptan: `tag_ids` (AND), `hidden_tag_ids` (exclusión), `search` (LIKE en filename), `folder` (prefijo de ruta), `untagged_only`, más `limit/offset/sort_field/sort_order`.

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
- Fecha, en este orden: EXIF (solo imágenes) → nombre del archivo → nombre de la carpeta padre → `mtime`.
  - ⚠️ Bug conocido: toma el primero de `DateTime`/`DateTimeOriginal`/`DateTimeDigitized` que aparezca; suele ser `DateTime` (fecha de *modificación*). Fase 1.
- `DATE_PATTERNS` está compilado y es estricto a propósito (tests en `tests/test_dates.py`):
  - El patrón compacto `YYYYMMDD` usa `(?<!\d)...(?!\d)` para **no** atrapar resoluciones (`1920x1080`, `19201080`).
  - Año válido 1990–2099. Mes fuera de 1–12 → se guarda solo el año.
  - Soporta `IMG_/VID_YYYYMMDD` y WhatsApp `IMG-YYYYMMDD-WA0001`.
- `pillow-heif` se registra con `try/import` al cargar el módulo.
- `extract_video_thumbnail()` solo delega a `thumbnail_cache.get_video_thumbnail()` (compatibilidad).
- El MD5 **no** se calcula al indexar (sería muy lento); se calcula bajo demanda desde el diálogo de duplicados.

---

## 9. Miniaturas (`thumbnail_cache.py`)

- Clave = `sha1(path + "::" + mtime)` → si el original cambia, la miniatura se regenera sola.
- Videos usan prefijo `v_` en la clave.
- API: `get_thumbnail(path, size)`, `get_video_thumbnail(path, size)`, `purge_orphans(known_paths)`, `cache_size_mb()`, `CACHE_DIR`.
- **Ojo:** el tamaño no forma parte de la clave. Si se pide la misma imagen a 200 px y a 480 px, gana la que se generó primero. Fase 1.
- **Ojo:** no aplica la orientación EXIF → fotos de celular giradas. Fase 1.

---

## 10. UI (`main.py`)

### Arranque
`__main__` → `setup_logging()` → `QApplication` → `install_qt_handlers()` → `_startup()` (backup diario + `init_db()`; si falla muestra el error y sale con código 1) → `MainWindow`.

### Hilos
- `IndexWorker` — indexación.
- `ThumbnailLoader` — carga miniaturas de la página actual; emite `loaded(photo_id, QPixmap)`.
- `MD5Worker` — calcula hashes faltantes.

**Reglas de hilos (causaron crashes `QThread: Destroyed while thread is still running`):**
- Antes de reemplazar un loader: desconectar señales → `stop()` → `wait()`. Usar `_stop_loader()`.
- `closeEvent` de la ventana principal detiene timers y loaders.
- No llamar una señal propia `done` en una subclase de `QDialog` (choca con `QDialog.done`). Por eso `QuickTagWindow` usa `done_signal`.
- ⚠️ Pendiente fase 1: `ThumbnailLoader` crea `QPixmap` fuera del hilo de UI (no permitido por Qt) y `_stop_loader` solo espera 500 ms.

### Rendimiento de la galería
- Widgets de miniatura se crean en lotes de 10 con un `QTimer(0)` (`_add_next_batch`) para no congelar la UI.
- Paginación (default 100 por página, configurable 10–500; **no se guarda entre sesiones**).
- `resizeEvent` con debounce de 400 ms; solo recarga si cambia el número de columnas.

### Ventana principal (`MainWindow`)
- Sidebar: botones de acciones, botón destacado **⚡ Etiquetado rápido**, filtros por tag agrupados por categoría (colapsables), botón 👁 para ocultar tags del sidebar, estadísticas.
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
| `DuplicatesDialog` | Calcula MD5 en hilo, agrupa duplicados, permite **borrar físicamente** copias (con confirmación, nunca la última copia). |
| `SettingsDialog` | Fotos por página, tamaño de caché, limpiar caché, purgar huérfanos. |
| `TagManagerDialog` | Crear/eliminar tags, flags hidden, exportar/importar JSON. |
| `CategoryManagerDialog` | Crear/renombrar/eliminar categorías (al eliminar, sus tags pasan a `general`). |
| `IndexDialog` / `DeindexDialog` | Indexar carpeta / quitar registros de una carpeta o de archivos que ya no existen. |

### Estilo visual
- Tema oscuro único definido en la constante `DARK_STYLE` (al final de `main.py`).
- Paleta: fondo `#0D0D1A`, paneles `#13131F` / `#1E1E2E`, bordes `#2D2D3F` / `#3A3A5A`, acento `#4A9EFF`, peligro `#FF4A4A`, advertencia `#FFD700`.
- Mantener esta paleta en cualquier UI nueva.

### Formato de exportación de tags (JSON)
```json
{ "version": 1,
  "tags": [{"name": "...", "category": "...", "color": "#RRGGBB", "hidden": false, "sidebar_hidden": false}],
  "categories": ["..."] }
```
La importación solo crea tags que no existan; nunca sobrescribe. ⚠️ No incluye las asignaciones foto↔tag (fase 2).

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
- `hiddenimports` incluye `PyQt6.QtSvg`, `PyQt6.QtSvgWidgets`, `cv2`, `pillow_heif`. Si agregas un import dinámico nuevo, añádelo ahí.
- `build/`, `dist/`, `.venv/` no se commitean.

### Tests
- `tests/conftest.py` tiene una fixture `autouse` que redirige `database.DB_PATH`, `backup.BACKUP_DIR` y `thumbnail_cache.CACHE_DIR` a `tmp_path`. **Ningún test debe tocar `~/.photovault`.**
- `make_legacy_db(path)` simula una DB de V1 para probar migraciones.
- Para probar contra datos reales: copiar la DB real con la API de backup (abriéndola `?mode=ro`) a un directorio temporal y apuntar `DB_PATH` ahí. Nunca contra la DB real.
- Si tocas `DATE_PATTERNS`: agregar casos válidos y falsos positivos en `tests/test_dates.py`.

### Lint
- `ruff` con reglas E, W, F, B, I, UP. Se ignoran a propósito las reglas del estilo compacto de la UI (`E701/E702`, alineación de `=`) hasta la fase 3.
- **No correr `ruff format`** todavía: reformatearía todo `main.py`. Se hará al dividirlo en la fase 3.
- `mypy`: línea base de **43 errores** (casi todos parámetros `= None` sin `Optional`, más el tipo de retorno de `get_all_photos_for_duplicates`). Se corrigen en la fase 3; no agregar errores nuevos.

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

El detalle y el orden están en `ROADMAP.md` (fases 1–3). Los más graves:

1. ⚠️ **Pérdida de datos:** `DeindexDialog._remove_missing` → `services.remove_missing_files()` borra **sin confirmar** todo registro cuyo archivo no existe. Con el disco `G:` desconectado borraría toda la colección y sus etiquetas (CASCADE). Además corre en el hilo de UI.
2. Duplicados se borran con `unlink` (permanente), no a la Papelera.
3. `ThumbnailLoader` crea `QPixmap` fuera del hilo de UI; `_stop_loader` solo espera 500 ms.
4. Miniaturas sin orientación EXIF; tamaño no incluido en la clave del caché.
5. Fecha EXIF usa `DateTime` en lugar de `DateTimeOriginal`.
6. `deindex_folder` y filtro `folder` usan `LIKE 'carpeta%'` (afecta `D:\Fotos2`; `_`/`%` son comodines).
7. Nombres con `&`/`<` rompen el SVG de estadísticas y los `QLabel` con HTML.
8. Tamaño de página no persiste; tags ocultos del sidebar no se pueden restaurar desde ahí; no se pueden editar tags existentes.
9. Código residual en `MainWindow._build_ui` (`if False: pass` / `for ... in []`); `get_all_photos_for_duplicates` con `object.__setattr__` inútil y retorno de tuplas.
10. Rendimiento: búsqueda sin debounce; commit por archivo al indexar; cada imagen se abre 2 veces; `get_photos_for_tagging` con `limit=99_999`; `_refresh_tag_ui` llama `get_all_tags()` en un bucle; `PhotoDetailDialog` carga el original completo.
11. `pillow-heif` no instalado → los `.HEIC` del usuario no tienen miniatura.

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
