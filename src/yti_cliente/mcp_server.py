"""Servidor MCP (stdio): cuatro herramientas sobre la cola y la biblioteca locales (§8.4)."""

import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncIterator

from mcp.server.mcpserver import MCPServer

from yti_cliente.config import ErrorConfig, cargar_config, claves_cargadas
from yti_cliente.ejecutor import (
    Contexto,
    ErrorLocal,
    activar,
    crear_contexto,
    enviar,
    estimacion,
    hay_activos,
    registrar,
)
from yti_cliente.registro import configurar_registro
from yti_cliente.youtube import UrlNoAdmitida, extraer_video_id

log = logging.getLogger(__name__)

INTERVALO_SONDEO_S = 180
_ESTADOS_ACTIVOS = ("por_enviar", "enviando", "enviado", "retenido")
_INSTRUCCIONES = (
    "Encola videos de YouTube para analizarlos (encolar_video), consulta el avance de la cola "
    "(ver_cola), lista la biblioteca local (listar_biblioteca) y lee un resultado "
    "(leer_resultado)."
)


class Servicio:
    def __init__(self, ctx: Contexto):
        self.ctx = ctx
        self._tareas: set[asyncio.Task] = set()

    async def _enviar_fondo(self, trabajo: dict) -> None:
        try:
            await asyncio.to_thread(enviar, self.ctx, trabajo)
        except Exception:
            # Sin esto la excepción quedaría sin recuperar; el trabajo sigue en la cola local.
            log.exception("Falló el envío en segundo plano del trabajo %s", trabajo.get("id_local"))

    async def encolar_video(
        self, url: str, objetivo: str | None = None, solo_transcripcion: bool = False
    ) -> str:
        """Encola un video de YouTube para su análisis o su transcripción y retorna de inmediato.

        El envío a la API sigue en segundo plano. `objetivo` es lo que quieres saber del video
        (máximo 1000 caracteres); con `solo_transcripcion` se pide solo la transcripción.
        Usa ver_cola para seguir el avance.
        """
        try:
            res = await asyncio.to_thread(
                registrar, self.ctx, url, objetivo=objetivo, solo_transcripcion=solo_transcripcion
            )
        except ErrorLocal as e:
            return e.mensaje
        if res.duplicado:
            return res.mensaje
        tarea = asyncio.create_task(self._enviar_fondo(res.trabajo))
        self._tareas.add(tarea)
        tarea.add_done_callback(self._tareas.discard)
        return (
            f"Trabajo {res.trabajo['id_local']} registrado; se envía en segundo plano. "
            f"Tiempo estimado: {res.estimacion}. Consulta el avance con ver_cola."
        )

    async def ver_cola(self) -> str:
        """Actualiza y muestra el estado de la cola local y baja a la biblioteca los resultados listos."""
        lineas: list[str] = []
        bajados: list[dict] = []
        try:
            resumen = await asyncio.to_thread(activar, self.ctx)
            bajados = resumen.bajados
            lineas.extend(f"Aviso: {a}" for a in resumen.avisos)
        except Exception as e:
            log.exception("No se pudo actualizar la cola")
            lineas.append(f"No se pudo actualizar la cola ({type(e).__name__}); se muestra el último estado.")
        try:
            trabajos = await asyncio.to_thread(self.ctx.cola.listar)
        except Exception as e:
            log.exception("No se pudo leer la cola")
            return f"No se pudo leer la cola local ({type(e).__name__})."
        pendientes = [t for t in trabajos if t.get("estado") != "en_biblioteca"]
        if not pendientes and not bajados and not lineas:
            return "La cola está vacía."
        for t in pendientes:
            estado = t.get("estado")
            detalle = t.get("objetivo") or "solo transcripción"
            linea = f"- {t.get('id_local')} ({t.get('video_id')}, {detalle}): {estado}"
            if estado in _ESTADOS_ACTIVOS:
                linea += f"; tiempo estimado: {estimacion(t.get('duracion_s'))}"
            if estado == "retenido" and t.get("enviar_despues"):
                linea += f"; se enviará después de {t['enviar_despues']}"
            if estado == "error":
                error = t.get("error") or {}
                linea += f" ({error.get('codigo')}): {error.get('mensaje')}"
            lineas.append(linea)
        if bajados:
            lineas.append("")
            lineas.append("Recién bajados (pídelos con leer_resultado):")
            for t in bajados:
                lineas.append(f"- {t.get('video_id')}: {t.get('objetivo') or 'solo transcripción'}")
        return "\n".join(lineas)

    async def listar_biblioteca(self, buscar: str | None = None) -> str:
        """Lista los videos de la biblioteca local; `buscar` filtra por título, canal u objetivo."""
        try:
            bib = self.ctx.biblioteca
            entradas = await asyncio.to_thread(bib.listar, buscar)
            return bib.formatear_lista(entradas)
        except OSError as e:
            log.exception("No se pudo leer la biblioteca")
            return f"No se pudo leer la biblioteca ({type(e).__name__})."

    async def leer_resultado(
        self, video: str, objetivo: str | None = None, contenido: str = "analisis"
    ) -> str:
        """Devuelve el análisis o la transcripción de un video de la biblioteca.

        `video` es el video_id, una URL de YouTube o parte del título. `contenido` es «analisis»
        o «transcripcion»; `objetivo` elige entre varios análisis del mismo video.
        """
        try:
            video = extraer_video_id(video)
        except UrlNoAdmitida:
            pass
        try:
            return await asyncio.to_thread(self.ctx.biblioteca.leer_resultado, video, objetivo, contenido)
        except OSError as e:
            log.exception("No se pudo leer el resultado")
            return f"No se pudo leer el resultado ({type(e).__name__})."

    async def esperar_fondo(self) -> None:
        while self._tareas:
            await asyncio.gather(*list(self._tareas))


async def ciclo_sondeo(servicio: Servicio, *, intervalo_s: float = INTERVALO_SONDEO_S) -> None:
    while True:
        await asyncio.sleep(intervalo_s)
        try:
            if await asyncio.to_thread(hay_activos, servicio.ctx):
                await asyncio.to_thread(activar, servicio.ctx)
        except Exception:
            log.exception("Falló el sondeo periódico; se reintenta en el próximo intervalo")


def construir_servidor(servicio: Servicio) -> MCPServer:
    @contextlib.asynccontextmanager
    async def lifespan(servidor: MCPServer) -> AsyncIterator[None]:
        tarea = asyncio.create_task(ciclo_sondeo(servicio))
        try:
            yield None
        finally:
            tarea.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tarea

    servidor = MCPServer("yt-insights", instructions=_INSTRUCCIONES, lifespan=lifespan)
    servidor.add_tool(servicio.encolar_video, name="encolar_video")
    servidor.add_tool(servicio.ver_cola, name="ver_cola")
    servidor.add_tool(servicio.listar_biblioteca, name="listar_biblioteca")
    servidor.add_tool(servicio.leer_resultado, name="leer_resultado")
    return servidor


def ejecutar() -> None:
    try:
        cfg = cargar_config()
    except ErrorConfig as e:
        print(f"Configuración inválida: {e}", file=sys.stderr)
        raise SystemExit(1) from None
    configurar_registro(claves_cargadas(cfg), modo_mcp=True)
    servidor = construir_servidor(Servicio(crear_contexto(cfg)))
    servidor.run(transport="stdio")
