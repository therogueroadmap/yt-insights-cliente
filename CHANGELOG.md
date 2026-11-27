# Changelog

All notable changes are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

## [Unreleased]

### Added
- Proyectos locales: el comando `yti proyecto` (`crear`, `lista`, `ver`, `agregar`, `quitar`, `editar`, `nota`, `borrar`) administra proyectos con videos, objetivo y notas.
- MCP: herramientas `listar_proyectos`, `leer_proyecto`, `crear_proyecto`, `agregar_a_proyecto` y `anotar_proyecto`, y el parámetro `proyecto` en `encolar_video`.

### Changed
- `--proyecto` guarda el id del proyecto, lo crea si no existe y usa su objetivo como objetivo por defecto.
- `proyectos/<slug>.md` pasa a ser una vista generada; los archivos antiguos se migran a `.yti/proyectos/<id>.json` y su original se respalda en `.yti/proyectos/importados/`.
- Pedir un análisis sin clave de Anthropic falla con `FALTA_CLAVE` y lista las opciones según el canal: en la terminal, configurar la clave o usar `--solo-transcripcion`; en el MCP, configurar la clave o pedir `solo_transcripcion=True` y analizar la transcripción en la sesión con `leer_resultado`. La solicitud nunca se cambia a solo transcripción en silencio.

### Fixed
- Una clave vacía o con solo espacios cuenta como no configurada (antes daba `CLAVE_MAL_FORMADA`).
- Una clave en blanco ya no convierte en `***` los espacios de los mensajes de error al ocultar las claves.

---

## [0.1.1] — 2026-11-26

### Fixed
- El cliente acepta la API 0.3.0 y posteriores (0.y.z desde 0.2.2). Antes solo admitía 0.2.x y dejaba los trabajos en `por_enviar` con el aviso de versión no compatible contra la API desplegada (0.3.0). 1.0 o mayor se sigue rechazando.

---

## [0.1.0] — 2026-11-18

Primera versión publicada.

### Added
- Estructura del proyecto con `uv`, aislamiento de pruebas (`conftest.py`) y escritura atómica de archivos.
- CLI `yti` (`config`, `encolar`, `estado`, `esperar`, `lista`) y servidor MCP stdio (`yti mcp`) con las herramientas `encolar_video`, `ver_cola`, `listar_biblioteca` y `leer_resultado`, sobre la misma biblioteca local y la misma cola.
- Ajuste `idioma` (`yti config --idioma`, `YTI_IDIOMA`): idioma de salida del análisis. Por defecto, el del sistema (en Windows vía `GetUserDefaultLocaleName`; respaldo `es`). Se envía siempre a la API, se guarda con cada trabajo de la cola y `encolar_video` (MCP) acepta un `idioma` explícito.
- `pydantic` declarado como dependencia directa (antes llegaba solo de forma transitiva a través de `mcp`).

### Fixed
- Los mensajes del logger de yt-dlp (`yti_cliente.ytdlp`, p. ej. «No supported JavaScript runtime») ya no se imprimen en la consola; siguen en `yti.log`.
- Correcciones de la revisión: el aviso de bajados no se pierde si falla la lectura de la cola, un trabajo que falla antes del POST vuelve a `por_enviar`, el apagado del servidor MCP espera los envíos en curso (con tope) y `listar_biblioteca` controla cualquier error con el mensaje redactado.
