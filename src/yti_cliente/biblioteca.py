"""Biblioteca local: markdown con front matter plano e `indice.md` regenerado.

No toma el candado: quien la llama (el ejecutor) lo sostiene.
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from yti_cliente.archivos import escribir_atomico

# R14: verificado > sin_verificacion > no_aplica > nula.
_RANGO_VERIFICACION = {"verificado": 3, "sin_verificacion": 2, "no_aplica": 1}


def normalizar_objetivo(objetivo: str | None) -> str:
    if not objetivo:
        return ""
    return re.sub(r"\s+", " ", objetivo.strip()).casefold()


def slug(texto: str, maximo: int) -> str:
    ascii_ = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    return s[:maximo].strip("-")


def _sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c)).casefold()


def texto_verificacion(estado: str | None, pct: int | None) -> str:
    if estado == "verificado":
        return f"verificado {pct} %" if pct is not None else "verificado"
    if estado == "sin_verificacion":
        return "sin verificación"
    if estado == "no_aplica":
        return "no aplica"
    if estado is None:
        return "—"
    return str(estado)


def _valor_fm(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (int, float)):
        return repr(valor)
    return json.dumps(str(valor), ensure_ascii=False)


def front_matter(datos: dict) -> str:
    lineas = ["---"]
    for clave, valor in datos.items():
        v = _valor_fm(valor)
        lineas.append(f"{clave}: {v}" if v else f"{clave}:")
    lineas.append("---")
    return "\n".join(lineas) + "\n"


def _parsear_valor(crudo: str):
    crudo = crudo.strip()
    if not crudo:
        return None
    try:
        return json.loads(crudo)
    except ValueError:
        return crudo


def leer_front_matter(texto: str) -> tuple[dict, str]:
    lineas = texto.split("\n")
    if not lineas or lineas[0].strip() != "---":
        return {}, texto
    datos: dict = {}
    for i in range(1, len(lineas)):
        if lineas[i].strip() == "---":
            cuerpo = "\n".join(lineas[i + 1 :])
            if cuerpo.startswith("\n"):
                cuerpo = cuerpo[1:]
            return datos, cuerpo
        clave, sep, valor = lineas[i].partition(":")
        if sep:
            datos[clave.strip()] = _parsear_valor(valor)
    return {}, texto  # sin cierre: no es front matter


def _leer(ruta: Path) -> tuple[dict, str]:
    try:
        return leer_front_matter(ruta.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}, ""


def _celda(texto: str) -> str:
    return str(texto).replace("\n", " ").replace("|", "\\|")


@dataclass
class EntradaVideo:
    video_id: str
    titulo: str
    canal: str
    fecha: str
    objetivos: list[str]
    verificacion: str
    carpeta: Path
    archivos: list[str]


class Biblioteca:
    def __init__(self, raiz: Path):
        self.raiz = Path(raiz)

    # --- carpetas ---

    def buscar_carpeta(self, video_id: str) -> Path | None:
        base = self.raiz / "videos"
        if not base.is_dir():
            return None
        exacta = base / video_id
        if exacta.is_dir():
            return exacta
        for c in sorted(base.glob(f"{glob_escape(video_id)}-*")):
            if c.is_dir():
                return c
        return None

    def carpeta_video(self, video_id: str, titulo: str | None) -> Path:
        existente = self.buscar_carpeta(video_id)
        if existente is not None:
            return existente
        corto = slug(titulo, 40) if titulo else ""
        nombre = f"{video_id}-{corto}" if corto else video_id
        return self.raiz / "videos" / nombre

    # --- transcripción ---

    def tiene_transcripcion(self, video_id: str) -> Path | None:
        carpeta = self.buscar_carpeta(video_id)
        if carpeta is None:
            return None
        ruta = carpeta / "transcripcion.md"
        return ruta if ruta.is_file() else None

    def escribir_transcripcion(self, video_id: str, titulo: str | None, front: dict, cuerpo: str) -> bool:
        existente = self.tiene_transcripcion(video_id)
        if existente is not None:
            previa = _leer(existente)[0].get("verificacion")
            if _RANGO_VERIFICACION.get(front.get("verificacion"), 0) < _RANGO_VERIFICACION.get(previa, 0):
                return False
        ruta = self.carpeta_video(video_id, titulo) / "transcripcion.md"
        escribir_atomico(ruta, front_matter(front) + "\n" + cuerpo)
        return True

    # --- análisis ---

    def _analisis(self, carpeta: Path) -> list[tuple[Path, dict]]:
        if not carpeta.is_dir():
            return []
        return [(p, _leer(p)[0]) for p in sorted(carpeta.glob("analisis-*.md"))]

    def buscar_analisis(self, video_id: str, objetivo: str | None) -> Path | None:
        carpeta = self.buscar_carpeta(video_id)
        if carpeta is None:
            return None
        buscado = normalizar_objetivo(objetivo)
        for ruta, fm in self._analisis(carpeta):
            if normalizar_objetivo(fm.get("objetivo")) == buscado:
                return ruta
        return None

    def escribir_analisis(
        self, video_id: str, titulo: str | None, objetivo: str | None, front: dict, cuerpo: str
    ) -> Path:
        existente = self.buscar_analisis(video_id, objetivo)
        if existente is not None:
            ruta = existente
        else:
            carpeta = self.carpeta_video(video_id, titulo)
            normalizado = normalizar_objetivo(objetivo)
            base = slug(objetivo, 50) if objetivo else ""
            ruta = carpeta / f"analisis-{base or 'general'}.md"
            if ruta.exists():  # mismo nombre, otro objetivo
                sufijo = hashlib.sha256(normalizado.encode("utf-8")).hexdigest()[:6]
                ruta = carpeta / f"analisis-{base or 'general'}-{sufijo}.md"
        escribir_atomico(ruta, front_matter(front) + "\n" + cuerpo)
        return ruta

    # --- listado e índice ---

    def _entradas(self) -> list[tuple[str, EntradaVideo]]:
        base = self.raiz / "videos"
        if not base.is_dir():
            return []
        salida = []
        for carpeta in sorted(p for p in base.iterdir() if p.is_dir()):
            trans = carpeta / "transcripcion.md"
            ft = _leer(trans)[0] if trans.is_file() else {}
            analisis = self._analisis(carpeta)
            fa = analisis[0][1] if analisis else {}
            obtenido = ft.get("obtenido") or max((a.get("obtenido") or "" for _, a in analisis), default="")
            fuente_verif = ft if trans.is_file() else fa
            entrada = EntradaVideo(
                video_id=ft.get("video_id") or fa.get("video_id") or carpeta.name,
                titulo=ft.get("titulo") or carpeta.name,
                canal=ft.get("canal") or "",
                fecha=obtenido[:10],
                objetivos=[(a.get("objetivo") or "general") for _, a in analisis],
                verificacion=texto_verificacion(
                    fuente_verif.get("verificacion"), fuente_verif.get("coincidencia_pct")
                ),
                carpeta=carpeta,
                archivos=sorted(p.name for p in carpeta.glob("*.md")),
            )
            salida.append((obtenido, entrada))
        salida.sort(key=lambda par: par[0], reverse=True)
        return salida

    def listar(self, buscar: str | None = None) -> list[EntradaVideo]:
        entradas = [e for _, e in self._entradas()]
        if not buscar or not buscar.strip():
            return entradas
        aguja = _sin_acentos(buscar.strip())
        return [
            e
            for e in entradas
            if aguja in _sin_acentos(" ".join([e.titulo, e.canal, *e.objetivos]))
        ]

    def resolver_video(self, texto: str) -> list[EntradaVideo]:
        entradas = self.listar()
        texto = texto.strip()
        exactas = [e for e in entradas if e.video_id == texto]
        if exactas:
            return exactas
        if not texto:
            return []
        aguja = _sin_acentos(texto)
        return [e for e in entradas if aguja in _sin_acentos(e.titulo)]

    def regenerar_indice(self) -> Path:
        lineas = [
            "# Biblioteca de yt-insights",
            "",
            "| Video | Título | Fecha | Objetivos | Verificación |",
            "|---|---|---|---|---|",
        ]
        for _, e in self._entradas():
            enlaces = []
            for ruta, fm in self._analisis(e.carpeta):
                etiqueta = _celda(fm.get("objetivo") or "general")
                enlaces.append(f"[{etiqueta}](videos/{e.carpeta.name}/{ruta.name})")
            objetivos = ", ".join(enlaces) or "solo transcripción"
            lineas.append(
                f"| [{_celda(e.video_id)}](videos/{e.carpeta.name}/) | {_celda(e.titulo)} | {e.fecha} "
                f"| {objetivos} | {_celda(e.verificacion)} |"
            )
        ruta = self.raiz / "indice.md"
        escribir_atomico(ruta, "\n".join(lineas) + "\n")
        return ruta

    def formatear_lista(self, entradas: list[EntradaVideo]) -> str:
        if not entradas:
            return "La biblioteca no tiene videos que coincidan."
        lineas = [
            "| Video | Título | Canal | Fecha | Objetivos | Verificación |",
            "|---|---|---|---|---|---|",
        ]
        for e in entradas:
            objetivos = ", ".join(e.objetivos) or "solo transcripción"
            lineas.append(
                f"| {_celda(e.video_id)} | {_celda(e.titulo)} | {_celda(e.canal)} | {e.fecha} "
                f"| {_celda(objetivos)} | {_celda(e.verificacion)} |"
            )
        return "\n".join(lineas)

    # --- lectura (§8.4) ---

    def leer_resultado(self, video: str, objetivo: str | None = None, contenido: str = "analisis") -> str:
        if contenido not in ("analisis", "transcripcion"):
            return f"Contenido no válido: «{contenido}». Las opciones válidas son: analisis, transcripcion."
        coincidencias = self.resolver_video(video)
        if not coincidencias:
            return (
                f"No hay videos que coincidan con «{video}». "
                "Usa listar_biblioteca para ver los videos disponibles."
            )
        if len(coincidencias) > 1:
            return (
                f"Hay {len(coincidencias)} videos que coinciden con «{video}». Indica el video_id de uno:\n\n"
                + self.formatear_lista(coincidencias)
            )
        entrada = coincidencias[0]
        carpeta = entrada.carpeta
        trans = carpeta / "transcripcion.md"
        analisis = self._analisis(carpeta)

        def con_pie(ruta: Path | None, texto: str) -> str:
            otros = [a for a in entrada.archivos if ruta is None or a != ruta.name]
            return f"{texto.rstrip()}\n\nOtros archivos de este video: {', '.join(otros) or 'ninguno'}"

        if contenido == "transcripcion":
            if not trans.is_file():
                return con_pie(None, f"El video {entrada.video_id} no tiene transcripción.")
            return con_pie(trans, trans.read_text(encoding="utf-8"))

        if objetivo is not None and objetivo.strip():
            ruta = self.buscar_analisis(entrada.video_id, objetivo)
            if ruta is None and normalizar_objetivo(objetivo) == "general":
                # «general» es la etiqueta que muestran listar_biblioteca e indice.md para el análisis sin objetivo
                ruta = self.buscar_analisis(entrada.video_id, None)
            if ruta is None:
                disponibles = ", ".join(entrada.objetivos) or "ninguno (solo transcripción)"
                return con_pie(
                    None,
                    f"No hay un análisis con el objetivo «{objetivo}». Objetivos disponibles: {disponibles}.",
                )
            return con_pie(ruta, ruta.read_text(encoding="utf-8"))

        if not analisis:
            if trans.is_file():
                return con_pie(trans, trans.read_text(encoding="utf-8"))
            return con_pie(None, f"El video {entrada.video_id} no tiene análisis ni transcripción.")
        if len(analisis) == 1:
            ruta = analisis[0][0]
            return con_pie(ruta, ruta.read_text(encoding="utf-8"))
        for ruta, fm in analisis:
            if not normalizar_objetivo(fm.get("objetivo")):
                return con_pie(ruta, ruta.read_text(encoding="utf-8"))
        lista = "\n".join(f"- {o}" for o in entrada.objetivos)
        return con_pie(
            None,
            f"El video {entrada.video_id} tiene varios análisis. Indica un objetivo entre:\n{lista}",
        )


def glob_escape(texto: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", texto)
