# yt-insights-cliente

Cliente local de yt-insights: terminal `yti` y servidor MCP stdio.

> **Versión:** 0.3.0

Estado: versión de prueba. Los comandos y el formato de la biblioteca pueden cambiar entre versiones, así que actualiza con frecuencia (ver [Actualización](#actualización)) y reporta lo que falle o resulte confuso (ver [Reportar un problema](#reportar-un-problema)).

## Para qué sirve

`yti` encola videos de YouTube contra la API de yt-insights, aporta desde tu equipo el testigo de subtítulos (bajado con tu IP), sondea los trabajos sin dejar ningún proceso residente y guarda los resultados como una biblioteca de archivos markdown que puedes leer y buscar sin servicios externos. También ofrece un servidor MCP por stdio para usar la misma cola desde un asistente.

Licencia: MIT (consulta `LICENSE`).

## Instalación

Requiere Python 3.11 o superior y [uv](https://docs.astral.sh/uv/). Si no tienes uv, instálalo y abre una terminal nueva:

```
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS o Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Después instala el cliente:

```
uv tool install git+https://github.com/therogueroadmap/yt-insights-cliente
```

Si el paquete se publica en PyPI, bastará con `uv tool install yt-insights-cliente`.

## Primeros pasos

Necesitas tres claves: la de yt-insights, la de Gemini y la de Anthropic. La de Gemini es obligatoria en todos los trabajos, incluso con `--solo-transcripcion`. La de Anthropic solo hace falta para el modo análisis.

```
yti config --clave yt-insights
yti config --clave gemini
yti config --clave anthropic
yti encolar https://www.youtube.com/watch?v=dQw4w9WgXcQ
yti esperar
yti lista
```

`yti esperar` termina cuando el trabajo está listo. Los resultados quedan en la biblioteca (`~/yt-insights` por defecto): abre la carpeta del video y lee `analisis-general.md` y `transcripcion.md`. Para usarlo desde un asistente, sigue la [guía MCP](docs/guia-mcp.md).

La URL de la API y la ruta de la biblioteca ya traen un valor por defecto: no hace falta configurarlas.

## Configuración: `yti config`

```
yti config                          # muestra la configuración con las claves enmascaradas
yti config --clave yt-insights      # pide la clave sin eco y la guarda
yti config --clave gemini
yti config --clave anthropic        # solo hace falta para el modo análisis
yti config --biblioteca ~/mi-biblioteca
yti config --api-url https://yt-insights-api-f8y7.onrender.com   # solo si te indican otra URL
yti config --idioma en              # idioma en que se redacta el análisis (ISO 639-1)
```

No pases claves como argumentos de línea de comandos: quedarían en el historial del shell. Por eso `--clave` las pide con entrada oculta. Las variables de entorno `YTI_API_KEY`, `YTI_GEMINI_KEY`, `YTI_ANTHROPIC_KEY`, `YTI_BIBLIOTECA`, `YTI_API_URL` y `YTI_IDIOMA` tienen prioridad sobre el archivo.

El análisis se redacta en el idioma de salida configurado, no en el del video. Por defecto es el idioma del sistema (código ISO 639-1, p. ej. `es`); si no se puede determinar, `es`. Se admite una región opcional (`pt-BR`, `es-419`). Cada trabajo guarda su idioma en la cola local, así que los reintentos usan el mismo.

### Dónde quedan `config.toml` y `yti.log`

Las rutas las decide `platformdirs`:

| Sistema | `config.toml` | `yti.log` |
|---|---|---|
| Windows | `%LOCALAPPDATA%\yt-insights-cliente\config.toml` | `%LOCALAPPDATA%\yt-insights-cliente\Logs\yti.log` |
| macOS | `~/Library/Application Support/yt-insights-cliente/config.toml` | `~/Library/Logs/yt-insights-cliente/yti.log` |
| Linux | `~/.config/yt-insights-cliente/config.toml` (o `$XDG_CONFIG_HOME`) | `~/.local/state/yt-insights-cliente/log/yti.log` (o `$XDG_STATE_HOME`) |

## Subcomandos

- `yti encolar <url> [--objetivo T] [--solo-transcripcion] [--sin-testigo] [--proyecto X] [--forzar]`: registra el trabajo y lo envía. Imprime el id local, el id remoto, el estado y la estimación. Con `--proyecto X` enlaza el video al proyecto (lo crea si no existe) y, si no pasas `--objetivo`, usa el objetivo del proyecto como objetivo por defecto. Si el video ya está en la cola o en la biblioteca, avisa y no envía nada, salvo que uses `--forzar`.

  ```
  yti encolar https://www.youtube.com/watch?v=dQw4w9WgXcQ --objetivo "Migrar a microservicios"
  ```

- `yti proyecto <acción>`: administra los proyectos locales (un proyecto agrupa videos, un objetivo y notas). Las acciones aceptan el nombre o el id (`p-...`) del proyecto, y los videos como URL o id de 11 caracteres:

  ```
  yti proyecto crear NOMBRE [--objetivo T]
  yti proyecto lista
  yti proyecto ver NOMBRE
  yti proyecto agregar NOMBRE VIDEO...   # crea el proyecto si no existe; solo enlaza, no encola
  yti proyecto quitar NOMBRE VIDEO
  yti proyecto editar NOMBRE [--nombre N] [--objetivo T]   # --objetivo "" borra el objetivo
  yti proyecto nota NOMBRE TEXTO
  yti proyecto borrar NOMBRE             # no toca los videos
  ```

- `yti estado`: hace una activación (envía lo pendiente, sondea lo enviado y baja lo completado) y muestra la tabla de la cola.

  ```
  yti estado
  ```

- `yti esperar [id] [--tope-min N]`: repite las activaciones hasta que el trabajo indicado, o todos los activos, terminen (tope de 30 minutos por defecto). Ctrl+C sale sin perder nada.

  ```
  yti esperar --tope-min 20
  ```

- `yti lista [--buscar T]`: videos de la biblioteca; `--buscar` filtra por título, canal u objetivo.

  ```
  yti lista --buscar microservicios
  ```

- `yti mcp`: arranca el servidor MCP por stdio. La configuración en cada host está en la [guía MCP](docs/guia-mcp.md).

Los errores salen por stderr en una línea, con código de salida 1.

## Estructura de la biblioteca

```
~/yt-insights/
├── indice.md
├── videos/
│   └── dQw4w9WgXcQ-como-migrar-a-microservicios/
│       ├── transcripcion.md
│       ├── analisis-general.md
│       └── analisis-migrar-a-microservicios.md
├── proyectos/
│   └── arquitectura.md
└── .yti/
    ├── version
    ├── candado
    ├── proyectos/<id>.json
    ├── trabajos/<id_local>.json
    └── testigos/<id_local>.vtt
```

- La carpeta de cada video es `<video_id>-<titulo-corto>`; el cliente la busca por el prefijo `<video_id>-`.
- `transcripcion.md` y `analisis-<slug>.md` llevan un front matter con los datos del trabajo (video, objetivo, verificación, costo).
- `indice.md` se regenera completo a partir de esos front matter.
- `.yti/proyectos/<id>.json` es la fuente de cada proyecto; `proyectos/<slug>.md` es una vista generada (se regenera sola, edítala con `yti proyecto`).
- Los `proyectos/*.md` de versiones anteriores se migran a JSON la primera vez que se usa un proyecto; el original queda respaldado en `.yti/proyectos/importados/`.
- `.yti/` es el estado interno de la cola: no lo edites a mano.

## Límites del servicio

- **Arranque en frío:** el servicio se duerme tras un rato sin uso. La primera petición puede tardar hasta 90 segundos mientras despierta; el cliente espera solo.
- **Tope diario:** cada clave de yt-insights tiene un tope de trabajos por día. Al alcanzarlo, el trabajo queda guardado en la cola local y se envía solo cuando el tope se renueva (el mensaje indica la hora).

## Errores frecuentes

| Código | Qué pasó | Qué hacer |
|---|---|---|
| `CLAVE_YTI_INVALIDA` | El servicio rechazó tu clave de yt-insights. | Revísala con `yti config --clave yt-insights`; si sigue fallando, pide una nueva. |
| `FALTA_CLAVE_GEMINI` / `GEMINI_AUTH` | Falta la clave de Gemini o Gemini la rechazó. | Revísala con `yti config --clave gemini`. |
| `FALTA_CLAVE_ANTHROPIC` / `CLAUDE_AUTH` | Falta la clave de Anthropic, es inválida o no tiene saldo. | Revísala con `yti config --clave anthropic`. |
| `TOPE_DIARIO` | Alcanzaste el tope diario. | Nada: el trabajo se envía solo cuando se renueva. |
| `CLAVES_NO_DISPONIBLES` | El servicio se reinició y perdió las claves del trabajo. | El cliente lo reenvía una vez solo; si vuelve a fallar, encola el video de nuevo. |
| `SERVICIO_NO_DISPONIBLE` | No se pudo conectar con el servicio. | El trabajo queda guardado y se reintenta en la próxima activación (`yti estado`). |
| `CONTRATO_INCOMPATIBLE` | Tu versión del cliente no entiende la respuesta del servicio. | Actualiza el cliente (ver [Actualización](#actualización)). |

Otros errores del servicio (por ejemplo, cuota de Gemini agotada) se muestran con su código y un mensaje en la tabla de `yti estado`.

## Reportar un problema

Envía a quien te dio acceso:

- Qué comando ejecutaste (o qué le pediste al asistente) y qué esperabas.
- El id local del trabajo y, si lo tiene, el id remoto (salen en `yti estado`).
- El archivo `yti.log` (ver [la tabla de rutas](#dónde-quedan-configtoml-y-ytilog)). El log no contiene claves, pero revísalo antes de enviarlo.

No envíes nunca tus claves ni el archivo `config.toml`.

## Actualización

```
uv tool upgrade yt-insights-cliente
```

Esto actualiza la terminal `yti`. El servidor MCP corre con `uvx` y puede seguir usando la versión anterior desde la caché. Para actualizarlo, ejecuta una vez el siguiente comando y reinicia el host por completo:

```
uvx --refresh --from git+https://github.com/therogueroadmap/yt-insights-cliente yti --help
```

yt-dlp envejece rápido: cuando YouTube cambie algo, la descarga del testigo puede fallar hasta que actualices el paquete.

## Más documentación

- [Guía MCP](docs/guia-mcp.md)
- [Prueba manual contra Render](docs/prueba-manual.md)
