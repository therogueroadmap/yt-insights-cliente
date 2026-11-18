"""Logs con redacción de claves. En modo MCP nada va a stdout."""

import logging
import re
import sys
from collections.abc import Iterable
from pathlib import Path

import platformdirs

_CABECERA = re.compile(r"(X-(?:API|Gemini|Anthropic)-Key\s*[:=]\s*)\S+", re.IGNORECASE)
# Logger del adaptador de yt-dlp (testigo.py): avisos como «No supported JavaScript runtime» no afectan al testigo.
LOGGER_YTDLP = "yti_cliente.ytdlp"
_FORMATO = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def redactar(texto: str, claves: Iterable[str]) -> str:
    texto = _CABECERA.sub(r"\1***", texto)
    for clave in sorted({c for c in claves if c}, key=len, reverse=True):
        texto = texto.replace(clave, "***")
    return texto


class FiltroSinYtdlp(logging.Filter):
    """Deja fuera de la consola los mensajes del logger de yt-dlp; el archivo los conserva."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name != LOGGER_YTDLP and not record.name.startswith(f"{LOGGER_YTDLP}.")


class FiltroClaves(logging.Filter):
    def __init__(self, claves: Iterable[str]):
        super().__init__()
        self._claves = [c for c in claves if c]

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redactar(record.getMessage(), self._claves)
        record.args = ()
        if record.exc_info:
            traza = logging.Formatter().formatException(record.exc_info)
            record.exc_text = redactar(traza, self._claves)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redactar(record.exc_text, self._claves)
        if record.stack_info:
            record.stack_info = redactar(record.stack_info, self._claves)
        return True


def configurar_registro(
    claves: Iterable[str], *, modo_mcp: bool = False, dir_logs: Path | None = None
) -> Path:
    # modo_mcp: el único destino de consola es stderr, igual que en modo normal;
    # nunca se agrega un handler a stdout.
    claves = list(claves)
    dir_logs = Path(dir_logs) if dir_logs is not None else platformdirs.user_log_path(
        "yt-insights-cliente", appauthor=False
    )
    dir_logs.mkdir(parents=True, exist_ok=True)
    ruta = dir_logs / "yti.log"

    raiz = logging.getLogger()
    for h in list(raiz.handlers):
        if getattr(h, "_yti", False):
            raiz.removeHandler(h)
            h.close()

    formato = logging.Formatter(_FORMATO)
    handlers: list[logging.Handler] = [
        logging.FileHandler(ruta, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ]
    handlers[0].setLevel(logging.INFO)
    handlers[1].setLevel(logging.WARNING)  # la consola solo muestra avisos y errores; el archivo, INFO
    handlers[1].addFilter(FiltroSinYtdlp())  # yt-dlp solo va al archivo
    for h in handlers:
        h.setFormatter(formato)
        h.addFilter(FiltroClaves(claves))
        h._yti = True  # type: ignore[attr-defined]
        raiz.addHandler(h)
    if raiz.level == logging.NOTSET or raiz.level > logging.INFO:
        raiz.setLevel(logging.INFO)
    return ruta
