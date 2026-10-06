# PhotoVault — Roadmap

Plan por fases. Los números (#N) identifican cada mejora y no cambian aunque se reordene.
Al terminar cada fase la app queda estable y se puede compilar un `.exe`.

Leyenda: ⭐ alto impacto · esfuerzo **[B]** bajo · **[M]** medio · **[A]** alto

---

## Fase 0 — Fundamentos ✅
*Objetivo: que ningún cambio posterior pueda perder datos o romper algo sin que te enteres.*

- [x] #78 ⭐ [B] Repo git en la raíz, historial V2…V11 reconstruido, `.gitignore`, CLAUDE.md dentro
  - [x] Subido a GitHub
  - [x] Carpetas `Fotos V2…V11` enviadas a la Papelera (se conserva `Fotos V1`: no está en el historial)
- [x] #13 ⭐ [B] Backup automático de la DB: diario (últimos 7) + obligatorio antes de migrar (últimos 3)
- [x] #74 ⭐ [B] Migraciones versionadas con `PRAGMA user_version` (verificado contra copia de la DB real)
- [x] #75 ⭐ [B] Logging a archivo rotativo + excepthook global + mensajes de Qt al log
- [x] #76 [B] Fuera los `except: pass` silenciosos y el `print` del indexador
- [x] #80 [B] `ruff` (sin avisos) y `mypy` configurados en `pyproject.toml` (mypy: línea base 43 errores → fase 3)
- [x] #79 (inicio) [M] `pytest` con 40 tests: migraciones, backups, `DATE_PATTERNS`

**Hecho cuando:** el código está en GitHub, hay backups diarios y los errores quedan registrados.

---

## Fase 1 — Fallos críticos → v1.1 ✅
*Objetivo: que lo que ya existe funcione bien.*

- [x] #1 ⭐ [B] "Eliminar faltantes": buscar en hilo → resumen → confirmar → borrar. Protege unidades desconectadas y unidades donde faltan todos los archivos
- [x] #2 ⭐ [B] Duplicados a la Papelera (`send2trash`) en lugar de `unlink`
- [x] #3 ⭐ [M] Hilos seguros: `QImage` en el worker; `retire_thread` en vez de `wait(500)`; workers cancelables; señal `finished` ya no se redefine
- [x] #4 ⭐ [B] Orientación EXIF en miniaturas (`exif_transpose`) y visor (`QImageReader.setAutoTransform`)
- [x] #5 ⭐ [B] Fecha EXIF priorizando `DateTimeOriginal`
- [x] #6 [B] Tamaño en la clave del caché de miniaturas (`THUMB_VERSION` 2)
- [x] #7 [B] Filtro de carpeta exacto (`folder_like_pattern`, `ESCAPE '!'`)
- [x] #8 [B] Escapar nombres en el SVG de estadísticas y en `QLabel`
- [x] #9 [B] Guardar el tamaño de página
- [x] #10 [B] Poder restaurar tags escondidos del sidebar
- [x] #11 [B] Editar tags existentes (nombre, color, categoría)
- [x] #12 [B] Código muerto fuera, `Photo` completo (+`md5`), contador nuevas/actualizadas
- [x] *(nuevo)* `pillow-heif` instalado y registrado también en `thumbnail_cache` (1.148 HEIC en la colección)
- [x] *(extra)* Confirmación al eliminar un tag y al des-indexar (con cuántas fotos tienen etiquetas)
- [x] *(extra)* Visor de detalle con `QImageReader` escalado (no carga el original completo)
- [x] *(extra)* Miniaturas de video ya no se amontonan en una sola subcarpeta del caché
- [x] 53 tests nuevos (93 en total); verificado contra copia de la DB real

**Pendiente para el usuario:** pulsar *Configuración → Purgar huérfanos* una vez, para liberar el caché de miniaturas viejo.

---

## Fase 2 — Protección de datos → v1.2 ✅

- [x] #14 ⭐ [M] Exportar/importar asignaciones foto↔tag (JSON v2; empareja por ruta o por nombre+tamaño)
- [x] #15 ⭐ [M] Reubicar carpeta/unidad sin perder etiquetas (vista previa con comprobación en disco; fusiona si ya se re-indexó)
- [x] #16 [M] Sidecars XMP opcionales (escribir, sincronizar todo, importar desde .xmp propios y de otros programas)
- [x] #17 [B] Papelera interna de 30 días (migración v2): des-indexar, faltantes y duplicados se pueden restaurar con sus etiquetas
- [x] *(extra)* `run_with_progress`: tareas largas con progreso y Cancelar
- [x] 37 tests nuevos (130 en total); verificado contra copia de la DB real

**Hecho cuando:** puedes cambiar de disco o reinstalar el PC sin perder nada. ✓

**Recomendado para el usuario:** *Gestionar etiquetas → Exportar JSON* y guardar el archivo fuera del PC (USB/nube).

---

## Fase 3 — Reestructuración del código ✅
*Objetivo: dejar el código listo para crecer. No cambia nada de lo que se ve en la app.*

- [x] #71 ⭐ [M] `main.py` (2.800 líneas) → paquete `ui/` con 13 módulos (el más grande: `folders.py`, ~590 líneas tras el formato)
- [x] #72 [B] Un solo constructor de filtros SQL (`PhotoFilter` + `_build_where`)
- [x] #73 [B] Todo el SQL dentro de `database.py`
- [x] #77 [B] `config.py` + QSS en `ui/dark.qss`
- [x] #79 [M] Tests de `database` (97 %) y `services` (92 %); 151 tests en total
- [x] `mypy` estricto (`check_untyped_defs`): de 24 errores a 0
- [x] `ruff format` + todas las reglas de lint activas
- [x] *(extra)* `clear_layout` (los layouts acumulaban espaciadores), `self.scroll` ya no tapa `QWidget.scroll()`, `ClickableRow`
- [x] *(extra)* `main.py` importa la UI después de configurar el logging; el `.exe` se compiló y probó

**Hecho cuando:** la app se comporta igual (23 pruebas de humo con copia de la DB real ✓), los archivos son chicos y hay buena cobertura ✓.

---

## Fase 4 — Rendimiento → v1.3 ✅
*Medido con una copia de la DB real (171.840 fotos en `G:`).*

| Operación | Antes | Después |
|---|---|---|
| Galería, página 1 | 30 ms | 0,8 ms |
| Galería, página 1000 (offset 100k) | 230 ms | 3,8 ms |
| Búsqueda por nombre | 50 ms (por tecla) | 12 ms (una vez, con debounce) |
| Lista de carpetas | 328 ms | 12 ms |
| Lista de unidades | 517 ms | 86 ms |
| Estadísticas | 116 ms | 76 ms |
| Re-indexar 457 archivos sin cambios | 14,4 s | 0,03 s |
| Primera re-indexación de todo `G:\Fotos` tras actualizar | ~90 min (estimado) | ~2 s |
| Miniatura ya en caché | 5,6 ms | 0,17 ms |
| Miniaturas en frío (mismas 80 fotos) | 35 ms/foto | 15 ms/foto (4 hilos) |
| MD5 para duplicados | 171.840 archivos | 64.694 (solo tamaños repetidos) |

- [x] #19 ⭐ [M] Indexación incremental (columna `mtime`, migración v3); registros viejos solo reciben el mtime
- [x] #20 ⭐ [B] `os.scandir`, una sola apertura por imagen, escritura por lotes de 500
- [x] #65 [M] Indexación en segundo plano con progreso y Cancelar en la barra de estado
- [x] #21 [B] Debounce de 300 ms en la búsqueda
- [—] #22 [M] FTS5 — **descartado tras medir**: con índices y debounce, buscar tarda 12 ms
- [—] #23 [M] Paginación keyset — **descartada tras medir**: la página 1000 tarda 4 ms
- [x] #28 [B] Índices de orden (fecha, nombre, tamaño, agregado, carpeta) + `synchronous=NORMAL`
- [x] #24 [M] Miniaturas en paralelo + precarga de la página siguiente
- [x] #25 [B] `QPixmapCache` (150 MB): volver a una página no regenera nada
- [x] #26 [M] Miniaturas de lo nuevo se generan solas tras indexar; botón *Generar todas*
- [x] #27 [B] Duplicados: solo se hashean los archivos de tamaño repetido (xxhash no hizo falta: el disco es el límite)
- [x] #29 [B] Carpetas (columna calculada indexada), unidades y totales en SQL
- [x] *(bug)* Etiquetado rápido "sin etiquetar" solo cargaba 99.999 de 171.546 fotos
- [x] *(bug)* El md5 no se invalidaba al cambiar el archivo
- [x] *(bug)* Paginación inestable con nombres repetidos
- [x] 22 tests nuevos (182 en total) + pruebas de humo con la DB real

**Pendiente para el usuario:** indexar `G:\Fotos` una vez (≈2 s) para que todos los registros tengan `mtime`; hay 3 fotos nuevas sin indexar.

---

## Fase 5 — Galería nueva y visor → v2.0

- [ ] #18 ⭐ [A] Galería virtualizada (`QListView` + modelo) con scroll infinito
- [ ] #68 [B] Ir a fecha/posición
- [ ] #35 [B] Slider de tamaño de miniatura
- [ ] #47 [M] Selección avanzada (Shift-rango, Ctrl+A, entre páginas)
- [ ] #70 [B] Atajos de teclado en la galería
- [ ] #34 [M] Menú contextual
- [ ] #33 [B] Abrir en Explorador / copiar ruta
- [ ] #38 [M] Arrastrar fotos hacia otras apps
- [ ] #30 ⭐ [M] Visor a pantalla completa (←/→, zoom, rotar)
- [ ] #31 ⭐ [M] Video integrado (QtMultimedia) y GIFs animados
- [ ] #32 [B] Panel de información (fecha, resolución, tamaño, ruta, cámara)
- [ ] #36 [M] Árbol de carpetas
- [ ] #37 [M] Línea de tiempo por año/mes

---

## Fase 6 — Organización y filtros → v2.1

- [ ] #39 ⭐ [M] Filtros AND / OR / NOT
- [ ] #40 ⭐ [B] Filtros por tipo, fecha, resolución, orientación, duración
- [ ] #41 [M] Búsquedas guardadas / álbumes inteligentes
- [ ] #42 [B] Contador por tag y buscador en el sidebar
- [ ] #43 [M] Renombrar y fusionar tags
- [ ] #44 [M] Alias y tags jerárquicos
- [ ] #45 [B] Favoritos y estrellas
- [ ] #46 [B] Notas por foto

---

## Fase 7 — Etiquetado rápido 2.0 → v2.2

- [ ] #48 ⭐ [B] Atajos configurables
- [ ] #49 [M] Más de 9 atajos
- [ ] #50 [B] Precargar la siguiente imagen
- [ ] #54 [B] Imagen a resolución de pantalla
- [ ] #51 [B] Repetir los tags de la foto anterior
- [ ] #52 [B] Retomar sesión
- [ ] #53 [B] Deshacer navega a la foto afectada

---

## Fase 8 — Experiencia de uso → v2.3

- [ ] #64 [B] Guardar ventana, orden y filtros entre sesiones
- [ ] #66 [B] Notificaciones tipo toast
- [ ] #67 [B] Pantalla de bienvenida
- [ ] #69 [M] Vigilar carpetas y auto-indexar

---

## Fase 9 — Privacidad → v2.4

- [ ] #61 [M] PIN para contenido oculto
- [ ] #62 [M] Caché de miniaturas cifrado/separado para lo oculto
- [ ] #63 [B] Modo pánico

---

## Fase 10 — Inteligencia local → v3.0
*Usar ONNX Runtime en vez de PyTorch; modelos como descarga aparte para no inflar el `.exe`.*

- [ ] #60 [B] Sugerir tags por carpeta (se puede adelantar)
- [ ] #59 [M] Casi-duplicados con hash perceptual
- [ ] #55 ⭐ [A] Búsqueda semántica con CLIP
- [ ] #56 [A] Sugerencia automática de tags + detector NSFW
- [ ] #58 [M] OCR en capturas de pantalla
- [ ] #57 [A] Caras y personas

---

## Fase 11 — Distribución *(continua)*

- [ ] #82 ⭐ [B] Modo `onedir` (arranque rápido) — *se puede adelantar*
- [ ] #83 [B] `.exe` más liviano (`opencv-python-headless`, sin `collect_data_files('PyQt6')` completo)
- [ ] #84 [B] Ícono, versión en el `.exe`, "Acerca de"
- [ ] #85 [M] Instalador (Inno Setup)
- [ ] #81 [M] CI en GitHub Actions: tests + build en cada release
- [ ] #86 [M] Aviso de versión nueva
- [ ] #87 [B] Tags predefinidos genéricos/opcionales

---

## Dependencias clave
- Fase 0 primero: migraciones y backups protegen todo lo demás.
- Fase 3 antes que la 5: no reescribir la galería dentro de un `main.py` de 2.000 líneas.
- Fase 4 prepara la 5: keyset y caché en memoria son la base de la galería virtualizada.
- Fases 6–9 son casi independientes entre sí.
