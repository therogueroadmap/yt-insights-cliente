"""Configuración del cliente: entorno > archivo TOML > defecto."""

import locale
import os
import re
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import platformdirs
import tomli_w

from yti_cliente.archivos import escribir_atomico

API_URL_POR_DEFECTO = "https://yt-insights-api-f8y7.onrender.com"
BIBLIOTECA_POR_DEFECTO = "~/yt-insights"
CLAVES = ("yt_insights", "gemini", "anthropic")

_VARIABLES = {
    "api_url": "YTI_API_URL",
    "biblioteca": "YTI_BIBLIOTECA",
    "idioma": "YTI_IDIOMA",
    "yt_insights": "YTI_API_KEY",
    "gemini": "YTI_GEMINI_KEY",
    "anthropic": "YTI_ANTHROPIC_KEY",
}
_CLAVE_PROVEEDOR = re.compile(r"[A-Za-z0-9._\-]{20,512}")
# Idioma de SALIDA del análisis: ISO 639-1/-2 en minúsculas y región opcional (es, es-419, pt-BR).
_IDIOMA = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?")
_IDIOMA_MAX = 12
IDIOMA_RESPALDO = "es"


class ErrorConfig(Exception):
    """Campo desconocido o archivo de configuración ilegible."""


@dataclass(frozen=True)
class Config:
    api_url: str
    biblioteca: Path
    clave_yti: str | None
    clave_gemini: str | None
    clave_anthropic: str | None
    origen: dict[str, str]
    idioma: str = IDIOMA_RESPALDO


def validar_idioma(valor: str) -> str:
    valor = valor.strip() if isinstance(valor, str) else ""
    if len(valor) > _IDIOMA_MAX or _IDIOMA.fullmatch(valor) is None:
        raise ErrorConfig(
            f"Idioma inválido: «{valor[:_IDIOMA_MAX + 1]}». Usa un código ISO 639-1 en minúsculas, "
            "con región opcional (p. ej. es, en, pt-BR, es-419)."
        )
    return valor


def _codigo_desde_locale(nombre: str | None) -> str | None:
    """Idioma base (`es`) de un nombre de locale (`es_ES.UTF-8`, `es-419`); None si no es un código."""
    if not nombre:
        return None
    base = re.split(r"[-_.@]", nombre, maxsplit=1)[0].lower()
    return base if re.fullmatch(r"[a-z]{2,3}", base) else None


def _locale_windows() -> str | None:
    """Nombre BCP 47 del usuario (`es-419`) vía GetUserDefaultLocaleName; None si no se puede."""
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(85)  # LOCALE_NAME_MAX_LENGTH
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
            return buffer.value
    except (AttributeError, OSError, ImportError):
        pass
    return None


def idioma_del_sistema() -> str:
    """Código ISO 639-1 del locale del sistema; `es` si no se puede determinar."""
    candidatos: list[str | None] = []
    if sys.platform == "win32":
        # getlocale() devuelve nombres como «Spanish_Spain», que no son ISO.
        candidatos.append(_locale_windows())
    try:
        candidatos.append(locale.getlocale()[0])
    except (ValueError, TypeError):
        pass
    candidatos.extend(os.environ.get(v) for v in ("LC_ALL", "LC_MESSAGES", "LANG"))
    for nombre in candidatos:
        codigo = _codigo_desde_locale(nombre)
        if codigo and codigo not in ("c", "po"):
            return codigo
    return IDIOMA_RESPALDO


def ruta_config() -> Path:
    return platformdirs.user_config_path("yt-insights-cliente", appauthor=False) / "config.toml"


def _leer(ruta: Path) -> dict:
    try:
        with open(ruta, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as e:
        raise ErrorConfig(f"No se pudo leer el archivo de configuración {ruta}: {type(e).__name__}") from e


def cargar_config(ruta: Path | None = None, entorno: Mapping[str, str] | None = None) -> Config:
    ruta = ruta if ruta is not None else ruta_config()
    entorno = os.environ if entorno is None else entorno
    datos = _leer(ruta)
    claves_archivo = datos.get("claves", {})
    if not isinstance(claves_archivo, dict):
        claves_archivo = {}

    def resolver(campo: str, defecto: str | None = None) -> tuple[str | None, str]:
        valor = entorno.get(_VARIABLES[campo]) or None
        if valor:
            return valor, "entorno"
        archivo = claves_archivo if campo in CLAVES else datos
        valor = archivo.get(campo)
        if isinstance(valor, str) and valor:
            return valor, "archivo"
        if defecto is not None:
            return defecto, "defecto"
        return None, "ausente"

    origen: dict[str, str] = {}
    valores: dict[str, str | None] = {}
    for campo, defecto in (
        ("api_url", API_URL_POR_DEFECTO),
        ("biblioteca", BIBLIOTECA_POR_DEFECTO),
        ("idioma", idioma_del_sistema()),
        ("yt_insights", None),
        ("gemini", None),
        ("anthropic", None),
    ):
        valores[campo], origen[campo] = resolver(campo, defecto)

    try:
        idioma = validar_idioma(valores["idioma"])
    except ErrorConfig as e:
        raise ErrorConfig(f"{e} (origen: {origen['idioma']})") from None

    return Config(
        api_url=valores["api_url"].rstrip("/"),
        biblioteca=Path(valores["biblioteca"]).expanduser(),
        idioma=idioma,
        clave_yti=valores["yt_insights"],
        clave_gemini=valores["gemini"],
        clave_anthropic=valores["anthropic"],
        origen=origen,
    )


def guardar_valor(campo: str, valor: str, ruta: Path | None = None) -> None:
    if campo not in CLAVES and campo not in ("api_url", "biblioteca", "idioma"):
        raise ErrorConfig(f"Campo de configuración desconocido: {campo}")
    if campo == "idioma":
        valor = validar_idioma(valor)
    ruta = ruta if ruta is not None else ruta_config()
    datos = _leer(ruta)
    if campo in CLAVES:
        claves = datos.get("claves")
        if not isinstance(claves, dict):
            claves = {}
        claves[campo] = valor
        datos["claves"] = claves
    else:
        datos[campo] = valor
    escribir_atomico(ruta, tomli_w.dumps(datos))
    if os.name == "posix":
        os.chmod(ruta, 0o600)


def validar_clave_proveedor(valor: str) -> bool:
    return _CLAVE_PROVEEDOR.fullmatch(valor) is not None


def enmascarar(valor: str | None) -> str:
    if not valor:
        return "(no configurada)"
    if len(valor) <= 8:
        return "****"
    return "…" + valor[-4:]


def claves_cargadas(cfg: Config) -> list[str]:
    return [c for c in (cfg.clave_yti, cfg.clave_gemini, cfg.clave_anthropic) if c]
