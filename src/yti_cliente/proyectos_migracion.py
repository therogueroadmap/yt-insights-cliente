"""Migración de los `proyectos/*.md` antiguos (escritos a mano) a proyectos con JSON.

Las funciones no toman el candado: lo sostiene quien llama.
"""

import logging
import re
from pathlib import Path

from yti_cliente.biblioteca import leer_front_matter
from yti_cliente.errores import ErrorLocal
from yti_cliente.limites import MAX_NOMBRE_PROYECTO, MAX_NOTA
from yti_cliente.proyectos import AVISO_VISTA, Proyectos, slug_proyecto

log = logging.getLogger(__name__)

_RE_ENLACE_VIDEO = re.compile(r"\s*(?:[-*+]\s+)?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\.\./videos/([^)/]+)/?\)\s*")
_RE_ID_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")
_MAX_REINTENTOS_NOMBRE = 1000


def _id_en_front(ruta: Path) -> str | None:
    try:
        front, _ = leer_front_matter(ruta.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    valor = front.get("video_id")
    return valor if isinstance(valor, str) and _RE_ID_VIDEO.fullmatch(valor) else None


def _video_id_de_carpeta(proyectos: Proyectos, carpeta: str) -> str | None:
    ruta = proyectos.biblioteca.raiz / "videos" / carpeta
    if carpeta not in (".", "..") and ruta.is_dir():
        candidatos = [ruta / "transcripcion.md", *sorted(ruta.glob("analisis-*.md"))]
        for c in candidatos:
            if (id_ := _id_en_front(c)):
                return id_
    prefijo = carpeta[:11]
    return prefijo if _RE_ID_VIDEO.fullmatch(prefijo) else None


def _partir(lineas: list[str]) -> list[str]:
    """Agrupa líneas en trozos de a lo sumo MAX_NOTA caracteres."""
    trozos: list[str] = []
    actual = ""
    for linea in lineas:
        while len(linea) > MAX_NOTA:
            if actual:
                trozos.append(actual)
                actual = ""
            trozos.append(linea[:MAX_NOTA])
            linea = linea[MAX_NOTA:]
        if actual and len(actual) + 1 + len(linea) > MAX_NOTA:
            trozos.append(actual)
            actual = ""
        actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        trozos.append(actual)
    return [t.strip() for t in trozos if t.strip()]


def _nombres(base: str):
    base = base.strip()[:MAX_NOMBRE_PROYECTO].strip()
    yield base
    for n in range(1, _MAX_REINTENTOS_NOMBRE + 1):
        sufijo = " (importado)" if n == 1 else f" (importado {n})"
        yield base[:MAX_NOMBRE_PROYECTO - len(sufijo)].rstrip() + sufijo


def _destino_importado(proyectos: Proyectos, nombre: str) -> Path:
    destino = proyectos.dir_importados / nombre
    n = 2
    while destino.exists():
        destino = proyectos.dir_importados / f"{Path(nombre).stem}-{n}.md"
        n += 1
    return destino


def _migrar_uno(proyectos: Proyectos, ruta: Path, texto: str) -> None:
    nombre = ruta.stem
    video_ids: list[str] = []
    omitidos: list[str] = []
    cuerpo: list[str] = []
    encabezado_visto = False
    for linea in texto.splitlines():
        if not encabezado_visto and linea.startswith("# "):
            encabezado_visto = True
            if linea[2:].strip():
                nombre = linea[2:].strip()
            continue
        if not linea.strip():
            continue
        m = _RE_ENLACE_VIDEO.fullmatch(linea)
        if m is None:
            cuerpo.append(linea.rstrip())
            continue
        id_ = _video_id_de_carpeta(proyectos, m.group(1))
        if id_ is None:
            omitidos.append(m.group(1))
        elif id_ not in video_ids:
            video_ids.append(id_)
    if omitidos:
        cuerpo.append("Videos omitidos al importar (no se pudo determinar su id): "
                      + ", ".join(omitidos))
    notas = _partir(cuerpo)

    p = None
    for candidato in _nombres(nombre):
        try:
            p = proyectos.crear(candidato)
            break
        except ErrorLocal as e:
            if e.codigo not in ("PROYECTO_YA_EXISTE", "PROYECTO_NOMBRE_INVALIDO"):
                raise
    if p is None:
        raise ErrorLocal("PROYECTO_YA_EXISTE", "no se encontró un nombre libre para importarlo.")
    try:
        for id_ in video_ids:
            proyectos.agregar_video(p.id, id_)
        for nota in notas:
            proyectos.anotar(p.id, nota, "importado")
        proyectos.dir_importados.mkdir(parents=True, exist_ok=True)
        ruta.replace(_destino_importado(proyectos, ruta.name))
    except (ErrorLocal, OSError):
        # Deshace solo el JSON: `borrar` también elimina la vista `<slug>.md`, que puede
        # ser el original del usuario. El original sigue intacto y se reintentará.
        try:
            proyectos._ruta(p.id).unlink(missing_ok=True)
        except OSError as e_limpieza:
            log.warning("no se pudo deshacer el proyecto %s: %s", p.id, e_limpieza)
        raise


def migrar_antiguos(proyectos: Proyectos) -> list[str]:
    """Convierte los `.md` antiguos de `proyectos/` en proyectos JSON. Devuelve los avisos."""
    return migrar_antiguos_contando(proyectos)[0]


def migrar_antiguos_contando(proyectos: Proyectos) -> tuple[list[str], int]:
    """Como `migrar_antiguos`, y además devuelve cuántos archivos se migraron."""
    if not proyectos.dir_vistas.is_dir():
        return [], 0
    try:
        existentes = {slug_proyecto(p.nombre) for p in proyectos.listar()}
    except ErrorLocal as e:
        return [f"no se migraron los proyectos antiguos: {e.mensaje}"], 0
    avisos: list[str] = []
    migrados = 0
    for ruta in sorted(proyectos.dir_vistas.glob("*.md")):
        if ruta.stem in existentes:
            continue
        try:
            texto = ruta.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            avisos.append(f"{ruta.name}: no se pudo leer; se deja sin migrar.")
            continue
        if AVISO_VISTA in texto:
            continue
        try:
            _migrar_uno(proyectos, ruta, texto)
            migrados += 1
        except ErrorLocal as e:
            avisos.append(f"{ruta.name}: no se migró ({e.mensaje})")
        except OSError as e:
            avisos.append(f"{ruta.name}: no se migró ({e})")
            log.warning("migración de %s falló: %s", ruta.name, e)
    return avisos, migrados
