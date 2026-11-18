"""Testigo: subtítulos que el cliente baja con yt-dlp usando la IP del usuario."""

import logging
import tempfile
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.networking.exceptions import HTTPError, TransportError

from yti_cliente import vtt

TOPE_BYTES = 2_097_152
_MAX_IDIOMA = 16
_MAX_MOTIVO = 300

_log = logging.getLogger("yti_cliente.ytdlp")


@dataclass
class InfoVideo:
    titulo: str | None
    canal: str | None
    duracion_s: int | None


@dataclass
class ResultadoTestigo:
    estado: str  # "obtenido" | "sin_subtitulos" | "fallido"
    contenido: str | None
    idioma: str | None
    motivo: str | None
    info: InfoVideo | None


class SinConexion(Exception):
    """No hubo red al hablar con YouTube."""


class _LoggerYtdlp:
    """Adaptador: yt-dlp nunca escribe en stdout/stderr, todo va a logging."""

    def debug(self, msg: str) -> None:
        _log.debug("%s", msg)

    def info(self, msg: str) -> None:
        _log.info("%s", msg)

    def warning(self, msg: str) -> None:
        _log.warning("%s", msg)

    def error(self, msg: str) -> None:
        _log.error("%s", msg)


def _opciones_base() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _LoggerYtdlp(),
    }


def elegir_pista(info: dict) -> tuple[str, str] | None:
    manuales = [k for k in (info.get("subtitles") or {}) if k != "live_chat"]
    autos = [k for k in (info.get("automatic_captions") or {}) if k != "live_chat"]
    idioma = info.get("language")
    if idioma:
        base = idioma.split("-")[0]
        for clave in (idioma, base):
            if clave in manuales:
                return clave, "manual"
        for clave in manuales:
            if clave.split("-")[0] == base:
                return clave, "manual"
        for clave in (f"{idioma}-orig", f"{base}-orig", idioma, base):
            if clave in autos:
                return clave, "auto"
    else:
        for clave in autos:
            if clave.endswith("-orig"):
                return clave, "auto"
    if manuales:
        return manuales[0], "manual"
    return None


def _es_error_de_red(exc: BaseException) -> bool:
    hay_red = False
    visitadas: set[int] = set()
    pendientes: list[BaseException | None] = [exc]
    while pendientes:
        actual = pendientes.pop()
        if actual is None or id(actual) in visitadas:
            continue
        visitadas.add(id(actual))
        if isinstance(actual, (HTTPError, urllib.error.HTTPError)):
            return False  # hubo respuesta HTTP (p. ej. 429 o 403 de YouTube): no es falta de red
        if isinstance(actual, (OSError, TransportError)):
            hay_red = True
        info = getattr(actual, "exc_info", None)
        if isinstance(info, tuple) and len(info) > 1:
            pendientes.append(info[1])
        pendientes.append(actual.__cause__)
        pendientes.append(actual.__context__)
    return hay_red


def _motivo(exc: Exception) -> str:
    texto = " ".join(str(exc).split())[:_MAX_MOTIVO]
    return texto or type(exc).__name__


def _info_video(info: dict) -> InfoVideo:
    duracion = info.get("duration")
    return InfoVideo(
        titulo=info.get("title"),
        canal=info.get("channel") or info.get("uploader"),
        duracion_s=int(duracion) if duracion is not None else None,
    )


def obtener_testigo(
    url: str,
    *,
    ydl_factory: Callable[[dict], Any] = yt_dlp.YoutubeDL,
) -> ResultadoTestigo:
    info_video: InfoVideo | None = None
    try:
        with ydl_factory(_opciones_base()) as ydl:
            info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict):
            return ResultadoTestigo("fallido", None, None, "yt-dlp no devolvió información del video", None)
        info_video = _info_video(info)

        pista = elegir_pista(info)
        if pista is None:
            return ResultadoTestigo("sin_subtitulos", None, None, None, info_video)
        clave, tipo = pista
        idioma = clave.removesuffix("-orig")[:_MAX_IDIOMA]

        with tempfile.TemporaryDirectory() as tmp:
            opts = _opciones_base()
            opts.update(
                skip_download=True,
                writesubtitles=tipo == "manual",
                writeautomaticsub=tipo == "auto",
                subtitleslangs=[clave],
                subtitlesformat="vtt",
                outtmpl=str(Path(tmp) / "sub"),
            )
            with ydl_factory(opts) as ydl:
                ydl.download([url])
            archivos = sorted(Path(tmp).glob("*.vtt"))
            crudo = archivos[0].read_bytes() if archivos else b""
    except Exception as exc:
        if _es_error_de_red(exc):
            raise SinConexion(_motivo(exc)) from exc
        return ResultadoTestigo("fallido", None, None, _motivo(exc), info_video)

    if len(crudo) > TOPE_BYTES:
        return ResultadoTestigo(
            "sin_subtitulos", None, idioma,
            f"El subtítulo supera el tope de {TOPE_BYTES} bytes", info_video,
        )
    contenido = crudo.decode("utf-8", errors="replace")
    if vtt.contar_segmentos(contenido) == 0:
        return ResultadoTestigo("sin_subtitulos", None, idioma, "el VTT no tiene segmentos", info_video)
    return ResultadoTestigo("obtenido", contenido, idioma, None, info_video)
