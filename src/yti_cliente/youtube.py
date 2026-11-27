"""Validación de URLs de YouTube y extracción del video_id."""

import re
from urllib.parse import parse_qs, urlsplit

HOSTS_ADMITIDOS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)

_PREFIJOS_RUTA = frozenset({"shorts", "embed", "live"})
_ID_VALIDO = re.compile(r"[A-Za-z0-9_-]{11}")


class UrlNoAdmitida(ValueError):
    """La URL no es de YouTube o no contiene un video_id válido."""


def _id_valido(candidato: str | None) -> str:
    if candidato is None or not _ID_VALIDO.fullmatch(candidato):
        raise UrlNoAdmitida("La URL no contiene un video_id válido de 11 caracteres.")
    return candidato


def extraer_video_id(url: str) -> str:
    try:
        partes = urlsplit(url.strip())
        host = partes.hostname
        _ = partes.port  # valida el puerto
    except ValueError as exc:
        raise UrlNoAdmitida("La URL no es válida.") from exc
    if partes.scheme.lower() not in ("http", "https"):
        raise UrlNoAdmitida("Solo se admiten URLs http o https.")
    if partes.username is not None or partes.password is not None:
        raise UrlNoAdmitida("No se admiten URLs con usuario.")
    if host is None or host.lower() not in HOSTS_ADMITIDOS:
        raise UrlNoAdmitida("El host no es de YouTube.")
    host = host.lower()
    segmentos = [s for s in partes.path.split("/") if s]
    if host == "youtu.be":
        return _id_valido(segmentos[0] if segmentos else None)
    if segmentos == ["watch"]:
        valores = parse_qs(partes.query).get("v")
        return _id_valido(valores[0] if valores else None)
    if len(segmentos) == 2 and segmentos[0] in _PREFIJOS_RUTA:
        return _id_valido(segmentos[1])
    raise UrlNoAdmitida("La ruta de la URL no corresponde a un video.")


def resolver_ref_video(texto: str) -> str:
    """Devuelve el video_id a partir de una URL admitida o de un id de 11 caracteres."""
    texto = texto.strip()
    if _ID_VALIDO.fullmatch(texto):
        return texto
    return extraer_video_id(texto)


def url_corta(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"
