"""Parser mínimo de WebVTT: devuelve pares (inicio en segundos, texto)."""

import html
import re

_TIEMPOS = re.compile(
    r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*\S+",
)
_ETIQUETAS = re.compile(r"<[^>]*>")
_VENTANA_REPETIDAS = 3


def _inicio(m: re.Match) -> float:
    horas, minutos, segundos, fraccion = m.groups()
    ms = int(fraccion.ljust(3, "0"))
    return int(horas or 0) * 3600 + int(minutos) * 60 + int(segundos) + ms / 1000


def parsear_vtt(contenido: str) -> list[tuple[float, str]]:
    texto = contenido.replace("\r\n", "\n").replace("\r", "\n")
    salida: list[tuple[float, str]] = []
    recientes: list[str] = []
    for bloque in re.split(r"\n\s*\n", texto):
        lineas = bloque.split("\n")
        idx = next((i for i, ln in enumerate(lineas) if _TIEMPOS.match(ln)), None)
        if idx is None:
            continue
        inicio = _inicio(_TIEMPOS.match(lineas[idx]))
        for ln in lineas[idx + 1 :]:
            limpia = html.unescape(_ETIQUETAS.sub("", ln)).strip()
            if not limpia or limpia in recientes:
                continue
            salida.append((inicio, limpia))
            recientes.append(limpia)
            del recientes[:-_VENTANA_REPETIDAS]
    return salida


def contar_segmentos(contenido: str) -> int:
    return len(parsear_vtt(contenido))
