import json
import logging
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from filelock import FileLock

from yti_cliente.archivos import escribir_atomico, reintentar_permiso

log = logging.getLogger(__name__)

ESQUEMA = 1
ESTADOS_ACTIVOS = ("por_enviar", "enviando", "retenido", "enviado")
TOMA_VENCE = timedelta(minutes=10)
_FORMATO_ISO = "%Y-%m-%dT%H:%M:%SZ"


class EsquemaMasNuevo(Exception):
    """La biblioteca fue escrita por un cliente con un esquema más nuevo."""


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_FORMATO_ISO)


def desde_iso(texto: str) -> datetime:
    return datetime.strptime(texto, _FORMATO_ISO).replace(tzinfo=timezone.utc)


def _campos_base() -> dict:
    return {
        "version": ESQUEMA, "id_local": None, "url": None, "video_id": None, "modo": None,
        "objetivo": None, "idioma": None, "objetivo_normalizado": None, "proyecto": None,
        "testigo_estado": None, "motivo_sin_testigo": None, "duracion_s": None,
        "titulo_local": None, "canal_local": None, "estado": None, "id_remoto": None,
        "estado_remoto": None, "intentos_remotos": 0, "enviar_despues": None,
        "reenvios_claves": 0, "tomado_por": None, "tomado_en": None, "error": None,
        "uso": None, "creado": None, "actualizado": None,
    }


class Cola:
    def __init__(self, biblioteca: Path, *, reloj: Callable[[], datetime] = ahora_utc,
                 id_proceso: str | None = None):
        self.raiz = Path(biblioteca) / ".yti"
        self.reloj = reloj
        self.id_proceso = id_proceso or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:6]}"
        self._candado = FileLock(str(self.raiz / "candado"), timeout=60)

    @property
    def _trabajos(self) -> Path:
        return self.raiz / "trabajos"

    @property
    def _testigos(self) -> Path:
        return self.raiz / "testigos"

    def candado(self) -> FileLock:
        self.raiz.mkdir(parents=True, exist_ok=True)
        return self._candado

    def _verificar_esquema(self) -> None:
        try:
            version = int((self.raiz / "version").read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            escribir_atomico(self.raiz / "version", f"{ESQUEMA}\n")
            return
        except ValueError:
            raise EsquemaMasNuevo("El archivo .yti/version es ilegible; actualiza yt-insights-cliente con «uv tool upgrade yt-insights-cliente».")
        if version > ESQUEMA:
            raise EsquemaMasNuevo(
                f"La biblioteca usa el esquema {version} y este cliente solo conoce el {ESQUEMA}; "
                "actualiza yt-insights-cliente con «uv tool upgrade yt-insights-cliente»."
            )

    def _ruta(self, id_local: str) -> Path:
        return self._trabajos / f"{id_local}.json"

    def _escribir(self, trabajo: dict) -> None:
        escribir_atomico(self._ruta(trabajo["id_local"]),
                         json.dumps(trabajo, ensure_ascii=False, indent=2) + "\n")

    def crear(self, datos: dict) -> dict:
        with self.candado():
            self._verificar_esquema()
            ahora = self.reloj()
            while True:
                id_local = f"{ahora.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
                if not self._ruta(id_local).exists():
                    break
            trabajo = {**_campos_base(), **datos}
            trabajo.update(
                id_local=id_local, estado="enviando", tomado_por=self.id_proceso,
                tomado_en=iso(ahora), creado=iso(ahora), actualizado=iso(ahora),
            )
            self._escribir(trabajo)
            return trabajo

    def leer(self, id_local: str) -> dict:
        ruta = self._ruta(id_local)
        return json.loads(reintentar_permiso(lambda: ruta.read_text(encoding="utf-8")))

    def guardar(self, trabajo: dict) -> None:
        with self.candado():
            self._verificar_esquema()
            trabajo["actualizado"] = iso(self.reloj())
            self._escribir(trabajo)

    def listar(self) -> list[dict]:
        trabajos = []
        for ruta in sorted(self._trabajos.glob("*.json")):
            try:
                trabajo = json.loads(reintentar_permiso(lambda: ruta.read_text(encoding="utf-8")))
                if not isinstance(trabajo, dict) or "id_local" not in trabajo:
                    raise ValueError("no es un trabajo")
                if ruta.stem != trabajo["id_local"]:
                    raise ValueError("el nombre no coincide con su id_local (¿copia de conflicto?)")
            except (OSError, ValueError) as e:
                log.warning("Se omite el archivo de cola %s: %s", ruta.name, e)
                continue
            trabajos.append(trabajo)
        return sorted(trabajos, key=lambda t: t["id_local"])

    def _tomable(self, t: dict, ahora: datetime) -> bool:
        try:
            if t.get("estado") == "por_enviar":
                return True
            if t.get("estado") == "retenido":
                return desde_iso(t["enviar_despues"]) <= ahora
            if t.get("estado") == "enviando":
                return ahora - desde_iso(t["tomado_en"]) > TOMA_VENCE
        except (KeyError, TypeError, ValueError):
            log.warning("Trabajo %s con fechas inválidas; no se toma", t.get("id_local"))
        return False

    def tomar_para_envio(self) -> list[dict]:
        with self.candado():
            self._verificar_esquema()
            ahora = self.reloj()
            tomados = []
            for t in self.listar():
                if not self._tomable(t, ahora):
                    continue
                t.update(estado="enviando", tomado_por=self.id_proceso, tomado_en=iso(ahora))
                self.guardar(t)
                tomados.append(t)
            return tomados

    def liberar(self, trabajo: dict, **cambios) -> dict:
        trabajo.update(cambios)
        trabajo["tomado_por"] = trabajo["tomado_en"] = None
        self.guardar(trabajo)
        return trabajo

    def actualizar_si(self, id_local: str, esperado: dict, cambios: dict) -> dict | None:
        with self.candado():
            try:
                actual = self.leer(id_local)
            except (OSError, ValueError):
                return None
            if any(actual.get(k) != v for k, v in esperado.items()):
                return None
            actual.update(cambios)
            self.guardar(actual)
            return actual

    def buscar_activo(self, video_id: str, objetivo_normalizado: str, modo: str) -> dict | None:
        for t in self.listar():
            if t.get("estado") not in ESTADOS_ACTIVOS or t.get("video_id") != video_id:
                continue
            if modo == "transcripcion":
                return t
            if t.get("modo") == "analisis" and t.get("objetivo_normalizado") == objetivo_normalizado:
                return t
        return None

    def _ruta_testigo(self, id_local: str) -> Path:
        return self._testigos / f"{id_local}.vtt"

    def guardar_testigo(self, id_local: str, contenido: str) -> None:
        escribir_atomico(self._ruta_testigo(id_local), contenido)

    def leer_testigo(self, id_local: str) -> str | None:
        try:
            return self._ruta_testigo(id_local).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def borrar_testigo(self, id_local: str) -> None:
        self._ruta_testigo(id_local).unlink(missing_ok=True)

    def hay_activos(self) -> bool:
        ahora = self.reloj()
        for t in self.listar():
            estado = t.get("estado")
            if estado in ("por_enviar", "enviando", "enviado"):
                return True
            if estado == "retenido" and self._tomable(t, ahora):
                return True
        return False
