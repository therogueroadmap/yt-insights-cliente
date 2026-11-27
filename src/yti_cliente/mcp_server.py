"""Servidor MCP (stdio): herramientas sobre la cola, la biblioteca y los proyectos locales (§8.4)."""

import asyncio
import base64
import contextlib
import logging
import sys
from collections.abc import AsyncIterator
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import Icon
from pydantic import Field

from yti_cliente import __version__
from yti_cliente.config import ErrorConfig, cargar_config, claves_cargadas
from yti_cliente.ejecutor import (
    Contexto,
    ErrorLocal,
    activar,
    con_proyectos,
    crear_contexto,
    enviar,
    estimacion,
    hay_activos,
    registrar,
)
from yti_cliente.proyectos_vista import texto_leer_proyecto
from yti_cliente.registro import configurar_registro, redactar
from yti_cliente.youtube import UrlNoAdmitida, extraer_video_id, resolver_ref_video

log = logging.getLogger(__name__)

INTERVALO_SONDEO_S = 180
# Igual al plazo HTTP por defecto de ClienteApi (api.py, plazo_s=30.0): máximo que se espera un envío al apagar.
TOPE_APAGADO_S = 30.0
_ESTADOS_ACTIVOS = ("por_enviar", "enviando", "enviado", "retenido")
_INSTRUCCIONES = (
    "Encola videos de YouTube para analizarlos (encolar_video), consulta el avance de la cola "
    "(ver_cola), lista la biblioteca local (listar_biblioteca) y lee un resultado "
    "(leer_resultado). Para investigar un tema con varios videos: crea el proyecto con su objetivo "
    "(crear_proyecto), encola cada video con `proyecto` (encolar_video), lee el proyecto con "
    "leer_proyecto y cada análisis con leer_resultado para sintetizar, y guarda la conclusión con "
    "anotar_proyecto. También puedes listar_proyectos y sumar un video ya analizado con "
    "agregar_a_proyecto. Quitar, editar y borrar proyectos no se hace desde aquí."
)


def _etiqueta(t: dict) -> str:
    if t.get("objetivo"):
        return t["objetivo"]
    return "general" if t.get("modo") == "analisis" else "solo transcripción"


class Servicio:
    def __init__(self, ctx: Contexto):
        self.ctx = ctx
        self._tareas: set[asyncio.Task] = set()
        self._bajados_sin_informar: list[dict] = []

    async def _enviar_fondo(self, trabajo: dict) -> None:
        try:
            await asyncio.to_thread(enviar, self.ctx, trabajo)
        except Exception:
            # Sin esto la excepción quedaría sin recuperar; el trabajo sigue en la cola local.
            log.exception("Falló el envío en segundo plano del trabajo %s", trabajo.get("id_local"))

    async def encolar_video(
        self,
        url: str,
        objetivo: str | None = None,
        solo_transcripcion: bool = False,
        idioma: Annotated[
            str | None,
            Field(
                description=(
                    "Idioma en que escribe el usuario en esta conversación, como código ISO 639-1 "
                    "(es, en, pt, fr...). El análisis se redactará en ese idioma. Pásalo siempre; "
                    "si se omite, se usa el idioma configurado en el cliente."
                )
            ),
        ] = None,
        proyecto: Annotated[
            str | None,
            Field(
                description=(
                    "Nombre del proyecto al que se suma el video. Se resuelve por nombre y se crea "
                    "si no existe; su objetivo es el objetivo por defecto del video."
                )
            ),
        ] = None,
    ) -> str:
        """Encola un video de YouTube para su análisis o su transcripción y retorna de inmediato.

        El envío a la API sigue en segundo plano. `objetivo` es lo que quieres saber del video
        (máximo 1000 caracteres); con `solo_transcripcion` se pide solo la transcripción.
        Usa ver_cola para seguir el avance.
        """
        try:
            res = await asyncio.to_thread(
                registrar, self.ctx, url, objetivo=objetivo, solo_transcripcion=solo_transcripcion,
                idioma=idioma, proyecto=proyecto, canal="mcp",
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

    async def _en_proyectos(self, accion, formato) -> str:
        """Corre `accion` con el candado en un hilo; devuelve el texto de `formato(resultado)` y el aviso de vistas."""
        try:
            resultado, aviso = await asyncio.to_thread(con_proyectos, self.ctx, accion)
        except ErrorLocal as e:
            return e.mensaje
        texto = formato(resultado)
        return f"{texto}\n{aviso}" if aviso else texto

    async def listar_proyectos(self) -> str:
        """Lista los proyectos locales con su objetivo y la cantidad de videos."""
        def formato(proyectos) -> str:
            if not proyectos:
                return "No hay proyectos."
            lineas = []
            for p in proyectos:
                n = len(p.videos)
                lineas.append(
                    f"- {p.nombre}: {p.objetivo or 'sin objetivo'} ({n} {'video' if n == 1 else 'videos'})"
                )
            return "\n".join(lineas)

        return await self._en_proyectos(lambda ps: ps.listar(), formato)

    async def leer_proyecto(self, proyecto: str) -> str:
        """Devuelve el objetivo, los videos y las notas de un proyecto (por nombre)."""
        return await self._en_proyectos(
            lambda ps: texto_leer_proyecto(ps, ps.resolver(proyecto)), lambda t: t
        )

    async def crear_proyecto(self, nombre: str, objetivo: str | None = None) -> str:
        """Crea un proyecto con un nombre y, si quieres, un objetivo de investigación."""
        return await self._en_proyectos(
            lambda ps: ps.crear(nombre, objetivo), lambda p: f"Proyecto «{p.nombre}» creado."
        )

    async def agregar_a_proyecto(self, proyecto: str, video: str) -> str:
        """Suma un video (URL o video_id) a un proyecto; si el proyecto no existe, lo crea."""
        try:
            video_id = resolver_ref_video(video)
        except UrlNoAdmitida as e:
            return str(e)

        def accion(ps) -> str:
            p, creado = ps.resolver_o_crear(proyecto)
            nuevo = ps.agregar_video(p.nombre, video_id)
            prefijo = f"Proyecto «{p.nombre}» creado. " if creado else ""
            return prefijo + (
                f"Video {video_id} agregado a «{p.nombre}»." if nuevo
                else f"El video {video_id} ya estaba en «{p.nombre}»."
            )

        return await self._en_proyectos(accion, lambda t: t)

    async def anotar_proyecto(self, proyecto: str, texto: str) -> str:
        """Guarda una nota (por ejemplo, la conclusión de una síntesis) en un proyecto."""
        return await self._en_proyectos(
            lambda ps: ps.anotar(proyecto, texto, "mcp"), lambda p: f"Nota guardada en «{p.nombre}»."
        )

    async def ver_cola(self) -> str:
        """Actualiza y muestra el estado de la cola local y baja a la biblioteca los resultados listos."""
        lineas: list[str] = []
        try:
            resumen = await asyncio.to_thread(activar, self.ctx)
            self._bajados_sin_informar.extend(resumen.bajados)
            lineas.extend(f"Aviso: {a}" for a in resumen.avisos)
        except Exception as e:
            log.exception("No se pudo actualizar la cola")
            lineas.append(f"No se pudo actualizar la cola ({type(e).__name__}); se muestra el último estado.")
        # Se vacía solo tras leer la cola con éxito; si falla, el aviso sigue pendiente.
        bajados = self._bajados_sin_informar
        try:
            trabajos = await asyncio.to_thread(self.ctx.cola.listar)
        except Exception as e:
            log.exception("No se pudo leer la cola")
            return f"No se pudo leer la cola local ({type(e).__name__})."
        self._bajados_sin_informar = []
        pendientes = [t for t in trabajos if t.get("estado") != "en_biblioteca"]
        if not pendientes and not bajados and not lineas:
            return "La cola está vacía."
        for t in pendientes:
            estado = t.get("estado")
            detalle = _etiqueta(t)
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
                lineas.append(f"- {t.get('video_id')}: {_etiqueta(t)}")
        return "\n".join(lineas)

    async def listar_biblioteca(self, buscar: str | None = None) -> str:
        """Lista los videos de la biblioteca local; `buscar` filtra por título, canal u objetivo."""
        try:
            bib = self.ctx.biblioteca
            entradas = await asyncio.to_thread(bib.listar, buscar)
            return bib.formatear_lista(entradas)
        except Exception as e:
            log.exception("No se pudo leer la biblioteca")
            cfg = self.ctx.cfg
            mensaje = redactar(str(e), (cfg.clave_yti, cfg.clave_gemini, cfg.clave_anthropic))
            return f"No se pudo leer la biblioteca ({type(e).__name__}: {mensaje})."

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

    def registrar_bajados(self, bajados: list[dict]) -> None:
        """Guarda los trabajos recién bajados para que el próximo ver_cola los anuncie."""
        self._bajados_sin_informar.extend(bajados)

    async def esperar_fondo(self, timeout: float | None = None) -> None:
        if timeout is not None:
            # Con tope no se cancelan los envíos en curso; los que queden tomados vencen solos.
            if self._tareas:
                await asyncio.wait(list(self._tareas), timeout=timeout)
            return
        while self._tareas:
            await asyncio.gather(*list(self._tareas))


async def ciclo_sondeo(servicio: Servicio, *, intervalo_s: float = INTERVALO_SONDEO_S) -> None:
    while True:
        await asyncio.sleep(intervalo_s)
        try:
            if await asyncio.to_thread(hay_activos, servicio.ctx):
                resumen = await asyncio.to_thread(activar, servicio.ctx)
                servicio.registrar_bajados(resumen.bajados)
        except Exception:
            log.exception("Falló el sondeo periódico; se reintenta en el próximo intervalo")


# Mismo icono que el favicon del panel (yt-insights/panel/index.html).
_ICONO_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='6' fill='#0a1b35'/>"
    "<circle cx='16' cy='16' r='9' fill='#f7792c'/>"
    "</svg>"
)
ICONO = Icon(
    src="data:image/svg+xml;base64," + base64.b64encode(_ICONO_SVG.encode("utf-8")).decode("ascii"),
    mime_type="image/svg+xml",
    sizes=["any"],
)


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
            await servicio.esperar_fondo(timeout=TOPE_APAGADO_S)

    servidor = MCPServer(
        "yt-insights",
        title="YouTube Insights",
        version=__version__,
        icons=[ICONO],
        instructions=_INSTRUCCIONES,
        lifespan=lifespan,
    )
    servidor.add_tool(servicio.encolar_video, name="encolar_video")
    servidor.add_tool(servicio.ver_cola, name="ver_cola")
    servidor.add_tool(servicio.listar_biblioteca, name="listar_biblioteca")
    servidor.add_tool(servicio.leer_resultado, name="leer_resultado")
    servidor.add_tool(servicio.listar_proyectos, name="listar_proyectos")
    servidor.add_tool(servicio.leer_proyecto, name="leer_proyecto")
    servidor.add_tool(servicio.crear_proyecto, name="crear_proyecto")
    servidor.add_tool(servicio.agregar_a_proyecto, name="agregar_a_proyecto")
    servidor.add_tool(servicio.anotar_proyecto, name="anotar_proyecto")
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
