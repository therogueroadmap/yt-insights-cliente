"""Ejecutor: registra un trabajo en la cola local y lo envía a la API (§9.2, §10.1)."""

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx
import platformdirs

from yti_cliente import testigo as _testigo
from yti_cliente.api import (
    ClaveYtiInvalida,
    ClienteApi,
    ErrorApi,
    RespuestaPerdida,
    ServicioNoDisponible,
    TestigoInvalido,
    TopeDiario,
    contrato_compatible,
)
from yti_cliente.biblioteca import Biblioteca, normalizar_objetivo
from yti_cliente.cola import Cola, ahora_utc, desde_iso, iso
from yti_cliente.config import Config, validar_clave_proveedor
from yti_cliente.testigo import ResultadoTestigo, SinConexion
from yti_cliente.youtube import UrlNoAdmitida, extraer_video_id

log = logging.getLogger(__name__)

MAX_OBJETIVO = 1000
_ESTIMACION_GENERICA = "entre 2 y 10 minutos según la duración"
_AVISO_VERSION = (
    "la API usa una versión no compatible; actualiza con `uv tool upgrade yt-insights-cliente`"
)
_REPORTAR = "es un fallo del cliente; reporta el problema"


class ErrorLocal(Exception):
    def __init__(self, codigo: str, mensaje: str):
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo
        self.mensaje = mensaje


class _TomaPerdida(Exception):
    """Otro proceso se quedó con el trabajo mientras este lo enviaba."""


@dataclass
class Contexto:
    cfg: Config
    cola: Cola
    biblioteca: Biblioteca
    reloj: Callable[[], datetime]
    obtener_testigo: Callable[[str], ResultadoTestigo]
    transport: httpx.BaseTransport | None
    ruta_cache_version: Path

    def api(self) -> ClienteApi:
        if not self.cfg.clave_yti:
            raise ErrorLocal(
                "FALTA_CLAVE",
                "Falta la clave de yt-insights; configúrala con `yti config --clave yt-insights`.",
            )
        return ClienteApi(self.cfg.api_url, self.cfg.clave_yti, transport=self.transport)


@dataclass
class ResultadoEncolar:
    duplicado: bool
    mensaje: str
    trabajo: dict | None
    estimacion: str


def crear_contexto(
    cfg: Config,
    *,
    reloj: Callable[[], datetime] | None = None,
    obtener_testigo: Callable[[str], ResultadoTestigo] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Contexto:
    reloj = reloj or ahora_utc
    return Contexto(
        cfg=cfg,
        cola=Cola(cfg.biblioteca, reloj=reloj),
        biblioteca=Biblioteca(cfg.biblioteca),
        reloj=reloj,
        obtener_testigo=obtener_testigo or _testigo.obtener_testigo,
        transport=transport,
        ruta_cache_version=platformdirs.user_state_path("yt-insights-cliente", appauthor=False)
        / "version.json",
    )


def estimacion(duracion_s: int | None) -> str:
    if duracion_s is None:
        return _ESTIMACION_GENERICA
    return f"unos {int(2 + duracion_s / 300 + 0.5)} minutos"


# --- registrar ---


def _verificar_claves(cfg: Config, modo: str) -> None:
    if not cfg.clave_yti:
        raise ErrorLocal(
            "FALTA_CLAVE",
            "Falta la clave de yt-insights; configúrala con `yti config --clave yt-insights`.",
        )
    if not cfg.clave_gemini:
        raise ErrorLocal(
            "FALTA_CLAVE",
            "Falta la clave de Gemini; configúrala con `yti config --clave gemini`.",
        )
    if modo == "analisis" and not cfg.clave_anthropic:
        raise ErrorLocal(
            "FALTA_CLAVE",
            "Falta la clave de Anthropic; configúrala con `yti config --clave anthropic` "
            "o pide solo la transcripción con `--solo-transcripcion`.",
        )
    proveedores = [("gemini", cfg.clave_gemini)]
    if modo == "analisis":
        proveedores.append(("anthropic", cfg.clave_anthropic))
    for nombre, valor in proveedores:
        if not validar_clave_proveedor(valor):
            raise ErrorLocal(
                "CLAVE_MAL_FORMADA",
                f"La clave de {nombre} tiene un formato inválido; "
                f"vuelve a configurarla con `yti config --clave {nombre}`.",
            )


def _aviso_duplicado(texto: str, url: str) -> str:
    return f"{texto}. Para enviarlo igualmente: yti encolar {url} --forzar"


def registrar(
    ctx: Contexto,
    url: str,
    *,
    objetivo: str | None = None,
    solo_transcripcion: bool = False,
    sin_testigo: bool = False,
    proyecto: str | None = None,
    forzar: bool = False,
) -> ResultadoEncolar:
    try:
        video_id = extraer_video_id(url)
    except UrlNoAdmitida as e:
        raise ErrorLocal("URL_NO_ADMITIDA", str(e)) from None
    objetivo = (objetivo or "").strip() or None
    if objetivo is not None and len(objetivo) > MAX_OBJETIVO:
        raise ErrorLocal(
            "OBJETIVO_LARGO", f"El objetivo supera los {MAX_OBJETIVO} caracteres."
        )
    modo = "transcripcion" if solo_transcripcion else "analisis"
    normalizado = normalizar_objetivo(objetivo)

    if not forzar:
        existente = (
            ctx.biblioteca.tiene_transcripcion(video_id)
            if solo_transcripcion
            else ctx.biblioteca.buscar_analisis(video_id, objetivo)
        )
        if existente is not None:
            if proyecto:
                ctx.biblioteca.agregar_a_proyecto(proyecto, video_id)
            return ResultadoEncolar(
                True,
                _aviso_duplicado(f"Ya está en la biblioteca: {existente}", url),
                None,
                estimacion(None),
            )
        activo = ctx.cola.buscar_activo(video_id, normalizado, modo)
        if activo is not None:
            if proyecto and not activo.get("proyecto"):
                ctx.cola.actualizar_si(
                    activo["id_local"], {"proyecto": activo.get("proyecto")}, {"proyecto": proyecto}
                )
            return ResultadoEncolar(
                True,
                _aviso_duplicado(f"Ya está en la cola con el id {activo['id_local']}", url),
                activo,
                estimacion(activo.get("duracion_s")),
            )

    _verificar_claves(ctx.cfg, modo)
    trabajo = ctx.cola.crear(
        {
            "url": url,
            "video_id": video_id,
            "modo": modo,
            "objetivo": objetivo,
            "objetivo_normalizado": normalizado,
            "proyecto": proyecto,
            "testigo_estado": "desactivado" if sin_testigo else "pendiente",
        }
    )
    return ResultadoEncolar(
        False, f"Trabajo {trabajo['id_local']} registrado.", trabajo, estimacion(None)
    )


# --- enviar ---


def _limpio(ctx: Contexto, texto: str) -> str:
    cfg = ctx.cfg
    for clave in (cfg.clave_yti, cfg.clave_gemini, cfg.clave_anthropic):
        if clave:
            texto = texto.replace(clave, "***")
    return texto


def _cambiar(ctx: Contexto, trabajo: dict, cambios: dict, *, liberar: bool) -> None:
    """Aplica `cambios` solo si este proceso sigue siendo quien tiene la toma."""
    if liberar:
        cambios = {**cambios, "tomado_por": None, "tomado_en": None}
    actualizado = ctx.cola.actualizar_si(
        trabajo["id_local"],
        {"estado": "enviando", "tomado_por": ctx.cola.id_proceso},
        cambios,
    )
    if actualizado is None:
        raise _TomaPerdida
    trabajo.clear()
    trabajo.update(actualizado)


def _error_final(ctx: Contexto, e: ErrorApi) -> dict:
    codigo = e.codigo
    mensaje = _limpio(ctx, e.mensaje)
    if isinstance(e, ClaveYtiInvalida):
        mensaje = "La clave de yt-insights es inválida o fue revocada; revisa `yti config`."
    elif codigo == "FALTA_CLAVE_GEMINI":
        mensaje = "Falta la clave de Gemini; configúrala con `yti config --clave gemini`."
    elif codigo == "FALTA_CLAVE_ANTHROPIC":
        mensaje = (
            "Falta la clave de Anthropic; configúrala con `yti config --clave anthropic` "
            "o usa `--solo-transcripcion`."
        )
    elif codigo == "CLAVE_MAL_FORMADA":
        mensaje = "Una clave tiene un formato inválido; revisa `yti config`."
    elif codigo in ("FUENTE_NO_ADMITIDA", "CALLBACK_NO_PERMITIDO"):
        mensaje = f"{mensaje} ({_REPORTAR})"
    return {"codigo": codigo, "mensaje": mensaje}


def _medianoche_siguiente(ahora: datetime) -> datetime:
    manana = ahora.astimezone(timezone.utc).date() + timedelta(days=1)
    return datetime.combine(manana, time(0, 0), tzinfo=timezone.utc)


def _testigo_a_enviar(ctx: Contexto, trabajo: dict) -> dict | None:
    if trabajo.get("testigo_estado") != "obtenido":
        return None
    contenido = ctx.cola.leer_testigo(trabajo["id_local"])
    if contenido is None:
        return None
    return {"formato": "vtt", "idioma": trabajo.get("testigo_idioma") or "und", "contenido": contenido}


def _obtener_testigo(ctx: Contexto, trabajo: dict) -> bool:
    """Devuelve False si no hay conexión (el trabajo queda `por_enviar`)."""
    try:
        res = ctx.obtener_testigo(trabajo["url"])
    except SinConexion:
        _cambiar(
            ctx, trabajo,
            {"estado": "por_enviar", "motivo_sin_envio": "sin conexión para obtener el testigo"},
            liberar=True,
        )
        return False
    info = res.info
    cambios = {
        "testigo_estado": res.estado,
        "motivo_sin_testigo": res.motivo,
        "titulo_local": info.titulo if info else None,
        "canal_local": info.canal if info else None,
        "duracion_s": info.duracion_s if info else None,
    }
    if res.estado == "obtenido" and res.contenido is not None:
        ctx.cola.guardar_testigo(trabajo["id_local"], res.contenido)
        cambios["testigo_idioma"] = res.idioma
    _cambiar(ctx, trabajo, cambios, liberar=False)
    return True


def _post(ctx: Contexto, api: ClienteApi, trabajo: dict) -> str:
    """POST con un único reenvío sin testigo si el servidor lo rechaza (R13)."""
    cfg = ctx.cfg
    testigo = _testigo_a_enviar(ctx, trabajo)
    while True:
        try:
            return api.crear_trabajo(
                url=trabajo["url"],
                modo=trabajo["modo"],
                objetivo=trabajo.get("objetivo"),
                testigo=testigo,
                clave_gemini=cfg.clave_gemini,
                clave_anthropic=cfg.clave_anthropic,
            )
        except TestigoInvalido:
            if testigo is None:
                raise
            testigo = None
            ctx.cola.borrar_testigo(trabajo["id_local"])
            _cambiar(
                ctx, trabajo,
                {
                    "testigo_estado": "sin_subtitulos",
                    "motivo_sin_testigo": "rechazado por el servidor (TESTIGO_INVALIDO)",
                },
                liberar=False,
            )


def _enviar(ctx: Contexto, trabajo: dict) -> dict:
    cfg = ctx.cfg
    try:
        _verificar_claves(cfg, trabajo["modo"])
    except ErrorLocal as e:
        _cambiar(ctx, trabajo, {"estado": "por_enviar", "motivo_sin_envio": e.mensaje}, liberar=True)
        raise
    if trabajo.get("testigo_estado") == "pendiente" and not _obtener_testigo(ctx, trabajo):
        return trabajo
    with ctx.api() as api:
        try:
            if not contrato_compatible(api, ctx.ruta_cache_version, ctx.reloj()):
                _cambiar(
                    ctx, trabajo,
                    {"estado": "por_enviar", "motivo_sin_envio": _AVISO_VERSION},
                    liberar=True,
                )
                return trabajo
            api.despertar()
            id_remoto = _post(ctx, api, trabajo)
        except TopeDiario as e:
            ahora = ctx.reloj()
            hasta = (
                ahora + timedelta(seconds=e.retry_after_s)
                if e.retry_after_s is not None
                else _medianoche_siguiente(ahora)
            )
            _cambiar(
                ctx, trabajo,
                {"estado": "retenido", "enviar_despues": iso(hasta), "motivo_sin_envio": None},
                liberar=True,
            )
        except RespuestaPerdida:
            _cambiar(
                ctx, trabajo,
                {"estado": "por_enviar", "motivo_sin_envio": "respuesta perdida"},
                liberar=True,
            )
        except ServicioNoDisponible:
            _cambiar(
                ctx, trabajo,
                {"estado": "por_enviar", "motivo_sin_envio": "servicio no disponible"},
                liberar=True,
            )
        except ErrorApi as e:
            error = _error_final(ctx, e)
            ctx.cola.borrar_testigo(trabajo["id_local"])
            _cambiar(
                ctx, trabajo, {"estado": "error", "error": error, "motivo_sin_envio": None},
                liberar=True,
            )
        else:
            _cambiar(
                ctx, trabajo,
                {
                    "estado": "enviado", "id_remoto": id_remoto, "estado_remoto": "pendiente",
                    "intentos_remotos": 0, "enviar_despues": None, "motivo_sin_envio": None,
                    "error": None,
                },
                liberar=True,
            )
    return trabajo


def enviar(ctx: Contexto, trabajo: dict) -> dict:
    """Pasos 5 y 6 de §9.2. Devuelve el trabajo actualizado, liberado de la toma."""
    try:
        return _enviar(ctx, trabajo)
    except _TomaPerdida:
        log.warning("El trabajo %s fue tomado por otro proceso; se abandona.", trabajo["id_local"])
        return trabajo


def _mensaje_resultado(trabajo: dict) -> str:
    estado = trabajo.get("estado")
    id_local = trabajo["id_local"]
    if estado == "enviado":
        return f"Trabajo {id_local} enviado."
    if estado == "retenido":
        local = desde_iso(trabajo["enviar_despues"]).astimezone().strftime("%Y-%m-%d %H:%M")
        return f"Trabajo {id_local}: tope diario alcanzado; se enviará a partir de {local}."
    if estado == "error":
        error = trabajo.get("error") or {}
        return f"Trabajo {id_local} con error ({error.get('codigo')}): {error.get('mensaje')}"
    motivo = trabajo.get("motivo_sin_envio") or "servicio no disponible"
    return f"Trabajo {id_local} guardado ({motivo}); se enviará en la próxima activación."


def encolar(ctx: Contexto, url: str, **kw) -> ResultadoEncolar:
    """`registrar` y, si no es duplicado, `enviar` (flujo de la terminal)."""
    res = registrar(ctx, url, **kw)
    if res.duplicado:
        return res
    trabajo = enviar(ctx, res.trabajo)
    return ResultadoEncolar(
        False, _mensaje_resultado(trabajo), trabajo, estimacion(trabajo.get("duracion_s"))
    )
