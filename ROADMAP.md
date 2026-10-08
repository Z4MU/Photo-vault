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

## Fase 5 — Galería nueva y visor → v2.0 ✅
*Medido con una copia de la DB real (171.840 fotos).*

| Operación | Antes (páginas de 100) | Después |
|---|---|---|
| Arranque hasta ver la galería | ~0,6 s + 130 ms por página | 0,5 s, toda la colección en un scroll |
| Saltar a la foto 100.000 y pintar | 1.000 clics de "Siguiente" | 11 ms (+0,4 s miniaturas en frío) |
| Seleccionar todo (Ctrl+A) | solo la página actual | 0,2 s (ids de 171.840 fotos: 85 ms) |
| Ir a una fecha (línea de tiempo) | — | 30 ms |
| Árbol de carpetas / filtrar una carpeta | — | 25 ms / 115 ms |
| Siguiente foto en el visor | diálogo nuevo por foto | 5 ms (precargada) |

- [x] #18 ⭐ [A] Galería virtualizada (`QListView` + modelo por tramos de 500 + cola LIFO de miniaturas): sin páginas
- [x] #68 [B] Ir a fecha (línea de tiempo) y a una posición (Ctrl+G); indicador "mes año · n / total"
- [x] #35 [B] Slider de tamaño de miniatura (100–400 px, se guarda; ≥ 240 usa las miniaturas de 480)
- [x] #47 [M] Selección como en el Explorador (clic, Ctrl, Shift, Ctrl+A) sobre toda la colección
- [x] #70 [B] Atajos de teclado (F1 los muestra)
- [x] #34 [M] Menú contextual
- [x] #33 [B] Mostrar en el Explorador / copiar rutas / copiar archivos / abrir con la app
- [x] #38 [M] Arrastrar fotos hacia otras apps (hasta 500)
- [x] #30 ⭐ [M] Visor a pantalla completa (←/→, zoom con rueda, 1:1, rotar la vista, precarga de vecinas)
- [x] #31 ⭐ [M] Video integrado (QtMultimedia/FFmpeg: MOV HEVC de iPhone y MP4 probados) y GIF animados
- [x] #32 [B] Panel de información (archivo, cámara/objetivo/ajustes EXIF, etiquetas editables)
- [x] #36 [M] Árbol de carpetas (filtro con chip ✕ arriba)
- [x] #37 [M] Línea de tiempo por año/mes (con los filtros actuales)
- [x] *(bug)* Nombres tipo UUID (`20547205-…`) daban años imposibles (2031–2054, 7 fotos): se rechazan y se releen al re-indexar
- [x] *(bug)* Los workers terminados se soltaban mientras cerraban su conexión (posible `QThread: Destroyed…`)
- [x] 39 tests nuevos (222 en total) + prueba de humo con la DB real
- Se quitó la opción "Fotos por página" (ya no hay páginas) y `PhotoDetailDialog` (lo reemplaza el visor).

**Pendiente para el usuario:** re-indexar `G:\Fotos` corrige las 7 fechas imposibles. 150 JPEG truncados (copias en "Broken pics") muestran ⚠ en vez de miniatura.

---

## Fase 6 — Organización y filtros → v2.1 ✅
*Medido con una copia de la DB real (171.843 fotos); migración a v4: 0,4 s.*

| Operación | Tiempo |
|---|---|
| Contar con ★★★ o más / favoritas / 2018–2020 | 1 ms |
| Contar verticales / ≥ 12 MP / videos < 30 s | 13–15 ms |
| Incluir 5 etiquetas (O) / excluir 5 etiquetas | 0,6 ms / 18 ms |
| Primera pantalla ordenada por valoración | 3 ms |
| Ctrl+A y ★★ a toda la colección | 0,9 s |
| Refrescar el panel de etiquetas (con contadores) | 47 ms |

- [x] #39 ⭐ [M] Filtros Y / O / NO: cada etiqueta del sidebar alterna incluir ✓ → excluir ✕ → nada; "todas (Y)" o "alguna (O)"
- [x] #40 ⭐ [B] Barra de filtros: tipo, años, orientación, resolución, duración, valoración, favoritas, sin etiquetar, con nota
- [x] #41 [M] Búsquedas guardadas (Ctrl+S) + álbumes inteligentes fijos (pestaña ⭐ Álbum, con contadores)
- [x] #42 [B] Contador de fotos por etiqueta y buscador (también por alias) en el sidebar y en el gestor
- [x] #43 [M] Fusionar etiquetas (el nombre viejo queda como alias; las búsquedas guardadas se actualizan)
- [x] #44 [M] Alias y etiquetas jerárquicas (filtrar por el padre incluye a las hijas; sin ciclos)
- [x] #45 [B] Favoritas (F) y estrellas (1–5, 0 quita) en galería, visor y menú; ordenar por valoración
- [x] #46 [B] Notas por foto (panel del visor; el buscador también busca en las notas)
- [x] Nada se pierde: papelera, reubicar, exportar JSON (v3) y `.xmp` (`xmp:Rating`, `cat|padre|hija`) llevan lo nuevo
- [x] *(bug)* Ancho/alto se guardaban sin aplicar la rotación EXIF (fotos de celular "horizontales"): corregido al indexar; opción **Releer todos los archivos** para lo ya indexado
- [x] *(bug)* `COLLATE NOCASE` solo ignora mayúsculas ASCII: "Mías" y "MÍAS" eran dos búsquedas distintas
- [x] 33 tests nuevos (255 en total) + prueba de humo con la DB real
- Visor: **Z** alterna ajustar/100 % (antes 0 y 1, ahora son estrellas) y **F** es favorita (pantalla completa: F11).

**Pendiente para el usuario:** indexar `G:\Fotos` con **Releer todos los archivos** (unos minutos) para que el filtro de orientación vea bien las fotos verticales de celular indexadas antes.

---

## Fase 7 — Etiquetado rápido 2.0 → v2.2 ✅
*Idea del usuario: elegir UNA etiqueta y una carpeta, y decidir foto por foto con el teclado si entra o no.
Medido con una copia de la DB real (171.843 fotos); migración a v5: 0,5 s.*

| Operación | Tiempo |
|---|---|
| Contar lo pendiente de una etiqueta en toda la colección | 5 ms |
| Armar la lista de pendientes (171.843) / retomar tras 3.000 respuestas | 75 ms / 105 ms |
| Siguiente foto en sí / no (ya precargada) | 2 ms |
| Primera foto a resolución de pantalla (disco despierto) | 28 ms |
| Cuadrícula de 16: primera página / página siguiente | 52 ms / 160 ms |
| Abrir "varias etiquetas" con toda la colección | 0,3 s (antes 0,8 s solo cargando) |

- [x] ⭐ Modo **sí / no** (idea del usuario): una etiqueta + un conjunto (colección, galería actual, álbum o búsqueda guardada, carpeta, solo sin etiquetar); → sí, ← no, Espacio saltar
- [x] ⭐ Modo **cuadrícula** (9/12/16/20): se marcan las que sí; al confirmar, el resto queda «no»
- [x] Los «no» se guardan (tabla `tag_rejections`, migración v5): una sesión nueva empieza por lo que falta; se exportan en el JSON
- [x] Se mantiene el modo **varias etiquetas**
- [x] #48 ⭐ Teclas configurables (⌨ Teclas…): cada acción con hasta 2 teclas, detección de choques
- [x] #49 Más de 9 atajos: cualquier tecla para cualquier etiqueta
- [x] #50 Precarga de las 3 siguientes (`PhotoStage`)
- [x] #54 Imagen a resolución de pantalla (máx. 2560 px), decodificada en un hilo
- [x] #51 Repetir las etiquetas de la foto anterior (Ctrl+R; un Ctrl+Z la deshace entera)
- [x] #52 Retomar sesión (sí / no y cuadrícula: automático; varias etiquetas: ofrece continuar en la última foto)
- [x] #53 Deshacer vuelve a la foto (o página) afectada
- [x] La configuración recuerda la última sesión (modo, etiqueta, conjunto, tamaño)
- [x] *(bug)* Un hilo de precarga quedaba vivo si una ventana se descartaba sin mostrarse → `retire_on_destroy`
- [x] 21 tests nuevos (276 en total) + prueba con la DB real y fotos de `G:`

---

## Fase 8 — Experiencia de uso → v2.3 ✅
*Medido con una copia de la DB real (171.843 fotos) y `G:\Fotos` como carpeta vigilada.*

| Operación | Antes | Después |
|---|---|---|
| Abrir con una carpeta vigilada (disco USB dormido) | 9,3 s con la ventana congelada | 0,25 s (se comprueba en un hilo) |
| Revisar `G:\Fotos` sin cambios al abrir | 23 s (una señal de progreso por archivo) | 0,8 s |
| Reabrir con filtros, orden y posición de la última vez | — | 0,14 s |

- [x] #64 [B] Recordar ventana, filtros, orden, carpeta, pestaña y posición (si ya no muestran nada, se abre con todo y avisa); opción en Configuración
- [x] #66 [B] Avisos tipo notificación (abajo a la derecha, se van solos; clic para cerrar)
- [x] #67 [B] Pantalla de bienvenida (colección vacía) y "sin resultados" con botón para limpiar filtros
- [x] #69 [M] Carpetas vigiladas: revisión al abrir y vigilancia mientras está abierta (30 s después del último cambio); las indexaciones esperan en cola
- [x] *(deuda #5)* "Limpiar caché" pide confirmación y corre en un hilo
- [x] *(bug)* Un aviso de "indexación terminada" que llegaba con la ventana ya cerrada arrancaba miniaturas y podía mostrar un error / cerrar la app (`_closing`)
- [x] *(bug)* La indexación mandaba una señal de progreso por archivo (saturaba la UI)
- [x] Tests: un `QMessageBox` inesperado o una excepción en un slot hacen fallar el test con su mensaje (antes: cuelgue o cierre sin saber dónde)
- [x] 11 tests nuevos (287 en total) + prueba con la DB real

---

## Fase 9 — Privacidad → v2.4 ✅
*Medido con una copia de la DB real (171.843 fotos) ocultando una etiqueta de 21 fotos.*

| Operación | Tiempo |
|---|---|
| Desbloquear (scrypt) + recargar la galería | 0,12 s + 0,18 s |
| Ocultar una etiqueta (borra sus miniaturas sin cifrar) | 31 ms |
| Miniatura cifrada ya en caché | 0,15 ms (igual que una normal) |
| Modo pánico (bloquear, cerrar ventanas, minimizar) | 0,2 s |

- [x] #61 [M] PIN para el contenido oculto: sin desbloquear no se ven las fotos ni (con PIN) los nombres de las etiquetas ocultas; código de recuperación; espera tras 5 errores; bloqueo automático por inactividad y al minimizar
- [x] #62 [M] Miniaturas de lo oculto cifradas (AES-GCM) en una carpeta aparte con nombres HMAC; las sin cifrar se borran
- [x] #63 [B] Modo pánico: tecla configurable que funciona desde cualquier ventana (también diálogos modales)
- [x] Configuración en pestañas
- [x] 26 tests nuevos (313 en total) + prueba con la DB real
- ⚠ Límite conocido: la base de datos no está cifrada (rutas y nombres de etiquetas se pueden leer con otro programa). Cifrarla (SQLCipher) sería otra fase.

---

## Fase 10 — Inteligencia local → v3.0 ✅ (sin OCR ni caras)
*Usar ONNX Runtime en vez de PyTorch; modelos como descarga aparte para no inflar el `.exe`.*

- [x] #60 [B] Sugerir tags por carpeta: por lo que tiene el resto de la carpeta (y la de arriba) y por su nombre; en el visor y en *Etiquetar selección* (~3 ms por foto)
- [x] #59 [M] Casi-duplicados con hash perceptual (pHash + bandas): *Duplicados → Parecidas*, 3 sensibilidades, ★ mejor calidad. Agrupar 174k hashes: 1,5 s. La primera vez hay que leer cada original (≈ 54 ms/foto desde el USB: ~2,5 h para 172k; cancelable y se retoma)
- [x] #55 ⭐ [A] Búsqueda por contenido con CLIP multilingüe (en español), botón 🧠 del buscador; "Parecidas por contenido" desde el menú de una foto. Modelo como descarga aparte (217 MB, verificado). Analizar: 37 ms por foto (≈ 1,8 h para 172k la primera vez); buscar: ~0,25 s
- [x] #56 [A] Etiquetas sugeridas por contenido (con 5+ fotos de ejemplo) y etiquetado rápido ordenado por parecido con la etiqueta o por "parece contenido adulto" (CLIP zero-shot)
- [ ] #58 [M] OCR en capturas de pantalla *(pospuesto: el usuario eligió no hacerlo en esta fase)*
- [ ] #57 [A] Caras y personas *(pospuesto)*

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
