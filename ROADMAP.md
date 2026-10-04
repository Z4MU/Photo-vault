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

## Fase 2 — Protección de datos → v1.2

- [ ] #14 ⭐ [M] Exportar/importar asignaciones foto↔tag (backup completo del etiquetado)
- [ ] #15 ⭐ [M] Reubicar carpeta/unidad (`D:\Fotos` → `E:\Fotos`) sin perder etiquetas
- [ ] #16 [M] Sidecars XMP opcionales
- [ ] #17 [B] ~~Advertir pérdida de etiquetas al des-indexar~~ (hecho en fase 1) / papelera interna

**Hecho cuando:** puedes cambiar de disco o reinstalar el PC sin perder nada.

---

## Fase 3 — Reestructuración del código
*Objetivo: dejar el código listo para crecer. No cambia nada de lo que se ve en la app.*

- [ ] #71 ⭐ [M] Dividir `main.py` en `ui/widgets`, `ui/dialogs`, `ui/workers`
- [ ] #72 [B] Un solo constructor de filtros SQL (hoy duplicado en `get_photos`/`get_photo_count`)
- [ ] #73 [B] Todo el SQL dentro de `database.py`
- [ ] #77 [B] `config.py` + QSS en archivo aparte
- [ ] #79 [M] Tests de `database` y `services`
- [ ] Corregir los 24 errores de `mypy` y endurecer la config
- [ ] `ruff format` sobre el código ya dividido

**Hecho cuando:** la app se comporta igual, ningún archivo pasa de ~500 líneas y hay buena cobertura en `database`/`services`.

---

## Fase 4 — Rendimiento → v1.3

- [ ] #19 ⭐ [M] Indexación incremental (columna `mtime`)
- [ ] #20 ⭐ [B] Indexación por lotes y una sola apertura por imagen
- [ ] #65 [M] Indexación en segundo plano y cancelable
- [ ] #21 [B] Debounce en la búsqueda
- [ ] #22 [M] Búsqueda con FTS5
- [ ] #23 [M] Paginación keyset
- [ ] #28 [B] Índice compuesto para ordenar por fecha + `PRAGMA synchronous=NORMAL`
- [ ] #24 [M] Miniaturas en paralelo (`QThreadPool`) + precarga
- [ ] #25 [B] Caché en memoria (`QPixmapCache`)
- [ ] #26 [M] Pre-generar miniaturas tras indexar
- [ ] #27 [B] Duplicados: agrupar por tamaño primero, luego xxhash
- [ ] #29 [B] Estadísticas y carpetas calculadas en SQL

**Hecho cuando:** re-indexar 100k archivos sin cambios tarda segundos y la UI nunca se congela.

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
