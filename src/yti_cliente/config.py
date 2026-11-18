"""Configuración del cliente: entorno > archivo TOML > defecto."""

import os
import re
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
    "yt_insights": "YTI_API_KEY",
    "gemini": "YTI_GEMINI_KEY",
    "anthropic": "YTI_ANTHROPIC_KEY",
}
_CLAVE_PROVEEDOR = re.compile(r"[A-Za-z0-9._\-]{20,512}")


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
        ("yt_insights", None),
        ("gemini", None),
        ("anthropic", None),
    ):
        valores[campo], origen[campo] = resolver(campo, defecto)

    return Config(
        api_url=valores["api_url"].rstrip("/"),
        biblioteca=Path(valores["biblioteca"]).expanduser(),
        clave_yti=valores["yt_insights"],
        clave_gemini=valores["gemini"],
        clave_anthropic=valores["anthropic"],
        origen=origen,
    )


def guardar_valor(campo: str, valor: str, ruta: Path | None = None) -> None:
    if campo not in CLAVES and campo not in ("api_url", "biblioteca"):
        raise ErrorConfig(f"Campo de configuración desconocido: {campo}")
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
