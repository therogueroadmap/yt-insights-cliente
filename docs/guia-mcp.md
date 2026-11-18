# Guía del servidor MCP de yt-insights

El comando `yti mcp` arranca un servidor MCP por stdio. Con él, el asistente de tu host (Claude Desktop, Claude Code, Cursor, etc.) puede encolar videos de YouTube, consultar la cola y leer los resultados de tu biblioteca local.

## 1. Configura las claves una sola vez con `yti config`

Ejecuta `yti config` en una terminal y guarda ahí tus claves (`yt-insights`, `gemini` y, para el modo análisis, `anthropic`). Después deja vacío el bloque `env` del host.

Motivo: los hosts MCP suelen guardar el archivo de configuración, y las variables de `env`, en texto plano. Una clave escrita ahí queda legible para cualquier programa o persona con acceso a ese archivo, y es fácil que acabe en una copia de seguridad o en un repositorio. El archivo de `yti config` se crea con permisos restringidos (`0600` en POSIX) y el servidor lo lee al arrancar.

Si aun así prefieres variables de entorno, las admitidas son `YTI_API_KEY`, `YTI_GEMINI_KEY` y `YTI_ANTHROPIC_KEY` (más `YTI_BIBLIOTECA` y `YTI_API_URL`). La variable de entorno tiene prioridad sobre el archivo.

## 2. Bloque de configuración

> Nota: mientras el paquete no esté publicado en PyPI, los bloques usan `uvx --from git+https://github.com/therogueroadmap/yt-insights-cliente yti mcp`. Al publicarlo, podrás usar `uvx --from yt-insights-cliente yti mcp`.

Con las claves ya guardadas por `yti config`, el bloque mínimo es este:

```json
{
  "mcpServers": {
    "yt-insights": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/therogueroadmap/yt-insights-cliente", "yti", "mcp"],
      "env": {}
    }
  }
}
```

Si prefieres pasar las claves por variables de entorno (queda en texto plano en el archivo del host):

```json
{
  "mcpServers": {
    "yt-insights": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/therogueroadmap/yt-insights-cliente", "yti", "mcp"],
      "env": {
        "YTI_API_KEY": "…",
        "YTI_GEMINI_KEY": "…",
        "YTI_ANTHROPIC_KEY": "…"
      }
    }
  }
}
```

Después de editar el archivo, reinicia el host por completo.

## 3. Configuración por host

### Claude Desktop

- Archivo: `claude_desktop_config.json`.
  - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
  - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
  - Linux: `~/.config/Claude/claude_desktop_config.json`
- También lo abres desde Ajustes, pestaña Developer, botón «Edit Config».
- Formato: JSON con la clave `mcpServers` (el bloque de la sección 2).
- Fuente consultada: https://modelcontextprotocol.io/docs/develop/connect-local-servers

### Claude Code

- Comando: `claude mcp add --transport stdio yt-insights -- uvx --from git+https://github.com/therogueroadmap/yt-insights-cliente yti mcp`
- Alcances (`--scope` o `-s`): `local` (por defecto, solo tú y el proyecto actual, en `~/.claude.json`), `user` (todos tus proyectos, en `~/.claude.json`) y `project` (archivo `.mcp.json` en la raíz del proyecto, compartido por control de versiones).
- Formato de `.mcp.json`: JSON con `mcpServers`, igual que la sección 2. No guardes claves en un `.mcp.json` que vayas a versionar.
- Fuente consultada: https://code.claude.com/docs/en/mcp

### Cursor

- Archivo: `~/.cursor/mcp.json` (global) o `.cursor/mcp.json` dentro del proyecto.
- Formato: JSON con `mcpServers`; la documentación indica el campo `type` como obligatorio para stdio, así que añade `"type": "stdio"` a la entrada.
- Fuente consultada: https://cursor.com/docs/context/mcp

### VS Code

- Archivo del espacio de trabajo: `.vscode/mcp.json`. También existe un `mcp.json` en el perfil de usuario; la documentación consultada no da su ruta exacta (por confirmar: ábrelo con el comando «MCP: Open User Configuration» de la paleta de comandos).
- Formato: JSON con la clave `servers` (no `mcpServers`) y `"type": "stdio"`:

```json
{
  "servers": {
    "yt-insights": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/therogueroadmap/yt-insights-cliente", "yti", "mcp"]
    }
  }
}
```

- Nota: la documentación indica que este arnés «Local» queda obsoleto en febrero de 2027 y recomienda los archivos nativos de Copilot (`~/.copilot/mcp-config.json` o `.mcp.json` en la raíz, con `mcpServers`). Revisa cuál usa tu versión.
- Fuentes consultadas: https://code.visualstudio.com/docs/agents/reference/mcp-configuration y https://code.visualstudio.com/docs/copilot/customization/mcp-servers

### Gemini CLI

- Archivo: `~/.gemini/settings.json` (usuario) o `.gemini/settings.json` (proyecto). `gemini mcp add` escribe en el del proyecto por defecto; con `-s user` escribe en el de usuario.
- Formato: JSON con `mcpServers` (el bloque de la sección 2). Los servidores stdio solo aparecen conectados en carpetas de confianza.
- Fuente consultada: https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md

### Codex CLI

- Archivo: `~/.codex/config.toml` (o `.codex/config.toml` en proyectos de confianza).
- Formato: TOML, no JSON:

```toml
[mcp_servers.yt-insights]
command = "uvx"
args = ["--from", "git+https://github.com/therogueroadmap/yt-insights-cliente", "yti", "mcp"]
```

- Alternativa: `codex mcp add yt-insights -- uvx --from git+https://github.com/therogueroadmap/yt-insights-cliente yti mcp`.
- Fuente consultada: https://learn.chatgpt.com/docs/extend/mcp?surface=cli (redirección desde https://developers.openai.com/codex/mcp)

### Windsurf

- Archivo: `mcp_config.json`, ruta por confirmar. La página oficial consultada (https://docs.windsurf.com/windsurf/cascade/mcp, que redirige a https://docs.devin.ai/desktop/cascade/mcp) ahora describe el producto sucesor y no documenta la ruta de Windsurf. Abre el archivo desde el panel de MCP de Cascade (icono de MCP y «Configure») para ver la ruta real en tu instalación.
- Formato: por confirmar; se espera JSON con `mcpServers`, igual que la sección 2.

## 4. Windows: ruta absoluta de `uvx`

Si el host no hereda el `PATH` de tu terminal (ocurre con varios hosts de escritorio), no encontrará `uvx` y el servidor no arrancará. En ese caso usa la ruta absoluta en `command`. Obtenla con:

```powershell
(Get-Command uvx).Source
```

y escríbela con las barras invertidas duplicadas en el JSON, por ejemplo `"command": "C:\\Users\\tu-usuario\\.local\\bin\\uvx.exe"`. Si estás en macOS o Linux, `which uvx` te da la ruta.

## 5. Herramientas

| Herramienta | Qué hace |
|---|---|
| `encolar_video(url, objetivo?, solo_transcripcion?)` | Registra el video en la cola local y vuelve de inmediato; el envío a la API sigue en segundo plano. Un duplicado devuelve un aviso: para forzarlo, hazlo desde la terminal con `yti encolar <url> --forzar`. |
| `ver_cola()` | Actualiza la cola, muestra el estado de cada trabajo con su tiempo estimado y baja a la biblioteca los resultados listos (sección «Recién bajados»). |
| `listar_biblioteca(buscar?)` | Lista los videos de la biblioteca; `buscar` filtra por título, canal u objetivo. |
| `leer_resultado(video, objetivo?, contenido?)` | Devuelve el análisis (`contenido="analisis"`, por defecto) o la transcripción (`"transcripcion"`). `video` puede ser un video_id, una URL o parte del título. |

Mientras el servidor está activo, sondea la cola cada 3 minutos (180 s) y solo si hay trabajos activos; así los resultados llegan a la biblioteca aunque no llames a `ver_cola`.

## 6. Recomendaciones

- No edites a mano la carpeta `.yti/` de la biblioteca: contiene la cola y el estado interno, y una edición manual puede dejar trabajos inconsistentes. Si algo se atasca, usa los comandos de `yti`.
- El servidor nunca escribe en stdout salvo el protocolo; sus logs van a stderr y al archivo de registro.
- Ninguna herramienta devuelve tus claves.
