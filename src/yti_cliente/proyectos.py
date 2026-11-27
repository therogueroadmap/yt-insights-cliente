"""Proyectos locales: un JSON por proyecto en `<raiz>/.yti/proyectos/<id>.json`.

No toma el candado de la cola: lo sostiene quien llama.
"""

import json
import logging
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from yti_cliente.archivos import escribir_atomico
from yti_cliente.biblioteca import Biblioteca, slug
from yti_cliente.cola import Cola, iso
from yti_cliente.errores import ErrorLocal
from yti_cliente.limites import MAX_NOMBRE_PROYECTO, MAX_NOTA, MAX_OBJETIVO

log = logging.getLogger(__name__)

AVISO_VISTA = "archivo generado; edítalo con `yti proyecto`"
VERSION = 1
SIN_CAMBIO = object()

_RE_ID = re.compile(r"p-[0-9a-f]{8}")
_RESERVADOS = {"con", "prn", "aux", "nul",
               *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_ORIGENES = {"cli", "mcp", "importado"}


def es_id_proyecto(ref: str) -> bool:
    return _RE_ID.fullmatch(ref) is not None


def slug_proyecto(nombre: str) -> str:
    s = slug(nombre, 50) or "proyecto"
    return f"{s}-p" if s in _RESERVADOS else s


@dataclass
class Proyecto:
    id: str
    nombre: str
    objetivo: str | None
    notas: list[dict] = field(default_factory=list)
    videos: list[dict] = field(default_factory=list)
    creado: str = ""
    actualizado: str = ""

    def a_dict(self) -> dict:
        return {"version": VERSION, "id": self.id, "nombre": self.nombre,
                "objetivo": self.objetivo, "notas": self.notas, "videos": self.videos,
                "creado": self.creado, "actualizado": self.actualizado}


class _Ilegible(Exception):
    pass


def _de_dict(ruta: Path, datos) -> Proyecto:
    if not isinstance(datos, dict) or ruta.stem != datos.get("id"):
        raise _Ilegible
    version = datos.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise _Ilegible
    if version > VERSION:
        raise ErrorLocal(
            "PROYECTO_VERSION_NUEVA",
            f"{ruta.name} fue escrito por una versión más nueva del cliente; "
            "actualiza yt-insights-cliente con «uv tool upgrade yt-insights-cliente».")
    objetivo = datos.get("objetivo")
    if (not isinstance(datos.get("nombre"), str)
            or not (objetivo is None or isinstance(objetivo, str))
            or not isinstance(datos.get("notas"), list)
            or not isinstance(datos.get("videos"), list)
            or not isinstance(datos.get("creado"), str)
            or not isinstance(datos.get("actualizado"), str)):
        raise _Ilegible
    return Proyecto(id=datos["id"], nombre=datos["nombre"], objetivo=objetivo,
                    notas=datos["notas"], videos=datos["videos"],
                    creado=datos["creado"], actualizado=datos["actualizado"])


class Proyectos:
    def __init__(self, biblioteca: Biblioteca, cola: Cola):
        self.biblioteca = biblioteca
        self.cola = cola

    @property
    def dir_json(self) -> Path:
        return self.biblioteca.raiz / ".yti" / "proyectos"

    @property
    def dir_vistas(self) -> Path:
        return self.biblioteca.raiz / "proyectos"

    @property
    def dir_importados(self) -> Path:
        return self.biblioteca.raiz / ".yti" / "proyectos-importados"

    def ruta_vista(self, p: Proyecto) -> Path:
        return self.dir_vistas / f"{slug_proyecto(p.nombre)}.md"

    def _ruta(self, id_: str) -> Path:
        return self.dir_json / f"{id_}.json"

    def _leer(self, ruta: Path) -> Proyecto:
        """Lee un JSON; `_Ilegible` si no cumple el formato, `ErrorLocal` si es de versión nueva."""
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise _Ilegible from None
        return _de_dict(ruta, datos)

    def _leer_todo(self) -> tuple[list[Proyecto], list[str]]:
        encontrados: list[Proyecto] = []
        ilegibles: list[str] = []
        if self.dir_json.is_dir():
            for ruta in sorted(self.dir_json.glob("*.json")):
                try:
                    encontrados.append(self._leer(ruta))
                except _Ilegible:
                    ilegibles.append(ruta.name)
        return encontrados, ilegibles

    def listar(self) -> list[Proyecto]:
        encontrados, ilegibles = self._leer_todo()
        for nombre in ilegibles:
            log.warning("proyecto ilegible omitido: %s", nombre)
        return sorted(encontrados, key=lambda p: (p.creado, p.id))

    def ilegibles(self) -> list[str]:
        return self._leer_todo()[1]

    def _exigir_sin_ilegibles(self) -> None:
        ilegibles = self.ilegibles()
        if ilegibles:
            raise ErrorLocal(
                "PROYECTO_ILEGIBLE",
                f"hay proyectos ilegibles ({', '.join(ilegibles)}); no se puede garantizar "
                "que el nombre sea único. Repáralos o muévelos fuera de .yti/proyectos.")

    def _guardar(self, p: Proyecto) -> None:
        p.actualizado = iso(self.cola.reloj())
        escribir_atomico(self._ruta(p.id),
                         json.dumps(p.a_dict(), ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def _validar_nombre(nombre: str) -> str:
        nombre = nombre.strip()
        if not nombre or len(nombre) > MAX_NOMBRE_PROYECTO:
            raise ErrorLocal("PROYECTO_NOMBRE_INVALIDO",
                             f"el nombre debe tener entre 1 y {MAX_NOMBRE_PROYECTO} caracteres.")
        if es_id_proyecto(nombre):
            raise ErrorLocal("PROYECTO_NOMBRE_INVALIDO",
                             "el nombre no puede tener la forma de un id de proyecto (p-xxxxxxxx).")
        return nombre

    @staticmethod
    def _validar_objetivo(objetivo: str | None) -> str | None:
        if objetivo is None:
            return None
        objetivo = objetivo.strip()
        if len(objetivo) > MAX_OBJETIVO:
            raise ErrorLocal("OBJETIVO_LARGO", f"el objetivo no puede superar {MAX_OBJETIVO} caracteres.")
        return objetivo or None

    def _exigir_slug_libre(self, nombre: str, excepto_id: str | None = None) -> None:
        s = slug_proyecto(nombre)
        for p in self.listar():
            if p.id != excepto_id and slug_proyecto(p.nombre) == s:
                raise ErrorLocal("PROYECTO_YA_EXISTE", f"ya existe el proyecto «{p.nombre}» ({p.id}).")

    def crear(self, nombre: str, objetivo: str | None = None) -> Proyecto:
        nombre = self._validar_nombre(nombre)
        objetivo = self._validar_objetivo(objetivo)
        self._exigir_sin_ilegibles()
        self._exigir_slug_libre(nombre)
        id_ = f"p-{secrets.token_hex(4)}"
        while self._ruta(id_).exists():
            id_ = f"p-{secrets.token_hex(4)}"
        ahora = iso(self.cola.reloj())
        p = Proyecto(id=id_, nombre=nombre, objetivo=objetivo, creado=ahora, actualizado=ahora)
        self._guardar(p)
        return p

    def resolver(self, ref: str) -> Proyecto:
        ref = ref.strip()
        if es_id_proyecto(ref):
            ruta = self._ruta(ref)
            if not ruta.exists():
                raise ErrorLocal("PROYECTO_NO_EXISTE", f"no existe el proyecto {ref}.")
            try:
                return self._leer(ruta)
            except _Ilegible:
                raise ErrorLocal("PROYECTO_ILEGIBLE", f"el archivo {ruta.name} está ilegible.") from None
        encontrados, ilegibles = self._leer_todo()
        s = slug_proyecto(ref)
        for p in encontrados:
            if slug_proyecto(p.nombre) == s:
                return p
        if ilegibles:
            raise ErrorLocal(
                "PROYECTO_ILEGIBLE",
                f"no se encontró «{ref}», pero hay archivos ilegibles ({', '.join(ilegibles)}).")
        raise ErrorLocal("PROYECTO_NO_EXISTE", f"no existe el proyecto «{ref}».")

    def resolver_o_crear(self, ref: str) -> tuple[Proyecto, bool]:
        try:
            return self.resolver(ref), False
        except ErrorLocal as e:
            if e.codigo != "PROYECTO_NO_EXISTE" or es_id_proyecto(ref.strip()):
                raise
        return self.crear(ref), True

    def agregar_video(self, ref: str, video_id: str) -> bool:
        p = self.resolver(ref)
        if any(v["video_id"] == video_id for v in p.videos):
            return False
        p.videos.append({"video_id": video_id, "agregado": iso(self.cola.reloj())})
        self._guardar(p)
        return True

    def quitar_video(self, ref: str, video_id: str) -> None:
        p = self.resolver(ref)
        restantes = [v for v in p.videos if v["video_id"] != video_id]
        if len(restantes) == len(p.videos):
            raise ErrorLocal("VIDEO_NO_EN_PROYECTO", f"el video {video_id} no está en «{p.nombre}».")
        p.videos = restantes
        self._guardar(p)

    def editar(self, ref: str, nombre: str | None = None, objetivo=SIN_CAMBIO) -> Proyecto:
        p = self.resolver(ref)
        vista_vieja = self.ruta_vista(p)
        cambio = False
        if objetivo is not SIN_CAMBIO:
            p.objetivo = self._validar_objetivo(objetivo)
            cambio = True
        if nombre is not None:
            nombre = self._validar_nombre(nombre)
            self._exigir_sin_ilegibles()
            self._exigir_slug_libre(nombre, excepto_id=p.id)
            p.nombre = nombre
            cambio = True
        if not cambio:
            return p
        self._guardar(p)
        if self.ruta_vista(p) != vista_vieja:
            vista_vieja.unlink(missing_ok=True)
        return p

    def anotar(self, ref: str, texto: str, origen: str) -> Proyecto:
        if origen not in _ORIGENES:
            raise ValueError(f"origen de nota inválido: {origen!r}")
        texto = texto.strip()
        if not texto:
            raise ErrorLocal("NOTA_VACIA", "la nota no puede estar vacía.")
        if len(texto) > MAX_NOTA:
            raise ErrorLocal("NOTA_LARGA", f"la nota no puede superar {MAX_NOTA} caracteres.")
        p = self.resolver(ref)
        p.notas.append({"fecha": iso(self.cola.reloj()), "texto": texto, "origen": origen})
        self._guardar(p)
        return p

    def borrar(self, ref: str) -> None:
        p = self.resolver(ref)
        self._ruta(p.id).unlink(missing_ok=True)
        self.ruta_vista(p).unlink(missing_ok=True)
