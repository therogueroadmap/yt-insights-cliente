"""Vista markdown generada de los proyectos y texto de `leer_proyecto`.

Ninguna función toma el candado: lo sostiene quien llama.
"""

import logging
from pathlib import Path

from yti_cliente.archivos import escribir_atomico
from yti_cliente.biblioteca import EntradaVideo, _celda, normalizar_objetivo
from yti_cliente.cola import ESTADOS_ACTIVOS
from yti_cliente.proyectos import AVISO_VISTA, Proyecto, Proyectos

log = logging.getLogger(__name__)

EN_COLA = "en cola"
EN_BIBLIOTECA = "en biblioteca"
CON_ERROR = "con error"
SIN_PEDIR = "sin pedir"


def _entradas(proyectos: Proyectos) -> dict[str, EntradaVideo]:
    return {e.video_id: e for e in proyectos.biblioteca.listar()}


def _trabajos(proyectos: Proyectos) -> dict[str, list[dict]]:
    por_video: dict[str, list[dict]] = {}
    for t in proyectos.cola.listar():
        if t.get("video_id"):
            por_video.setdefault(t["video_id"], []).append(t)
    return por_video


def _estado(video_id: str, entradas: dict[str, EntradaVideo], trabajos: dict[str, list[dict]]) -> str:
    propios = trabajos.get(video_id, [])
    if any(t.get("estado") in ESTADOS_ACTIVOS for t in propios):
        return EN_COLA
    entrada = entradas.get(video_id)
    if entrada is not None and any(
            a == "transcripcion.md" or (a.startswith("analisis-") and a.endswith(".md"))
            for a in entrada.archivos):
        return EN_BIBLIOTECA
    if propios:
        ultimo = max(propios, key=lambda t: t.get("id_local") or "")
        if ultimo.get("estado") not in (*ESTADOS_ACTIVOS, "en_biblioteca"):
            return CON_ERROR
    return SIN_PEDIR


def estado_video(proyectos: Proyectos, video_id: str) -> str:
    """Uno de: `en cola`, `en biblioteca`, `con error`, `sin pedir`."""
    return _estado(video_id, _entradas(proyectos), _trabajos(proyectos))


def _enlace(proyectos: Proyectos, p: Proyecto, entrada: EntradaVideo | None) -> tuple[bool, str]:
    """(existe el análisis del objetivo, ruta relativa al enlace o «»)."""
    if entrada is None:
        return False, ""
    if p.objetivo:
        propio = proyectos.biblioteca.buscar_analisis(entrada.video_id, p.objetivo)
        if propio is not None:
            return True, f"../videos/{entrada.carpeta.name}/{propio.name}"
    otro = next((a for a in entrada.archivos if a.startswith("analisis-")), None)
    if otro is None and "transcripcion.md" in entrada.archivos:
        otro = "transcripcion.md"
    return False, f"../videos/{entrada.carpeta.name}/{otro}" if otro else ""


def _lineas_notas(p: Proyecto) -> list[str]:
    return [f"- {n.get('fecha', '')} ({n.get('origen', '')}): "
            f"{str(n.get('texto', '')).replace(chr(10), chr(10) + '  ')}" for n in p.notas]


def _texto_vista(proyectos: Proyectos, p: Proyecto, entradas: dict[str, EntradaVideo],
                 trabajos: dict[str, list[dict]]) -> str:
    lineas = [AVISO_VISTA, "", f"# {p.nombre}", ""]
    if p.objetivo:
        lineas += [f"Objetivo: {p.objetivo}", ""]
    if p.notas:
        lineas += ["## Notas", "", *_lineas_notas(p), ""]
    lineas += ["## Videos", "", "| Título | Estado | Objetivo | Verificación | Enlace |",
               "|---|---|---|---|---|"]
    for v in p.videos:
        vid = v["video_id"]
        entrada = entradas.get(vid)
        titulo = entrada.titulo if entrada else vid
        tiene_objetivo, ruta = _enlace(proyectos, p, entrada)
        objetivo = ("sí" if tiene_objetivo else "no") if p.objetivo else "—"
        verificacion = entrada.verificacion if entrada and entrada.verificacion else "—"
        enlace = f"[{_celda(Path(ruta).name)}]({ruta})" if ruta else "—"
        lineas.append(f"| {_celda(titulo)} | {_estado(vid, entradas, trabajos)} | {objetivo} "
                      f"| {_celda(verificacion)} | {enlace} |")
    return "\n".join(lineas) + "\n"


def texto_vista(proyectos: Proyectos, proyecto: Proyecto) -> str:
    return _texto_vista(proyectos, proyecto, _entradas(proyectos), _trabajos(proyectos))


def regenerar_vistas(proyectos: Proyectos) -> list[Path]:
    """Escribe una vista por proyecto legible y devuelve las rutas escritas.

    No pisa un `.md` existente que no sea una vista generada.
    """
    entradas = _entradas(proyectos)
    trabajos = _trabajos(proyectos)
    escritas: list[Path] = []
    for p in proyectos.listar():
        ruta = proyectos.ruta_vista(p)
        if ruta.is_file():
            try:
                previo = ruta.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                previo = ""
            if AVISO_VISTA not in previo:
                log.warning("no se sobrescribe %s: no es una vista generada", ruta)
                continue
        escribir_atomico(ruta, _texto_vista(proyectos, p, entradas, trabajos))
        escritas.append(ruta)
    return escritas


def texto_leer_proyecto(proyectos: Proyectos, proyecto: Proyecto) -> str:
    entradas = _entradas(proyectos)
    trabajos = _trabajos(proyectos)
    p = proyecto
    lineas = [f"# {p.nombre}", ""]
    if p.objetivo:
        lineas += [f"Objetivo: {p.objetivo}", ""]
    if p.notas:
        lineas += ["## Notas", "", *_lineas_notas(p), ""]
    lineas += ["## Videos", ""]
    if not p.videos:
        lineas.append("(sin videos)")
    objetivo_proy = normalizar_objetivo(p.objetivo) if p.objetivo else None
    for v in p.videos:
        vid = v["video_id"]
        entrada = entradas.get(vid)
        titulo = entrada.titulo if entrada else vid
        lineas.append(f"- {vid} — {titulo} — {_estado(vid, entradas, trabajos)}")
        if entrada is None:
            continue
        if entrada.objetivos:
            etiquetados = [
                f"{o} (objetivo del proyecto)" if objetivo_proy is not None
                and normalizar_objetivo(o) == objetivo_proy else o
                for o in entrada.objetivos]
            lineas.append(f"  objetivos: {'; '.join(etiquetados)}")
            elegido = next((o for o in entrada.objetivos
                            if objetivo_proy is not None and normalizar_objetivo(o) == objetivo_proy),
                           entrada.objetivos[0])
            lineas.append(f'  leer_resultado(video="{vid}", objetivo="{elegido}")')
        elif "transcripcion.md" in entrada.archivos:
            lineas.append("  solo transcripción")
            lineas.append(f'  leer_resultado(video="{vid}", contenido="transcripcion")')
    return "\n".join(lineas) + "\n"
