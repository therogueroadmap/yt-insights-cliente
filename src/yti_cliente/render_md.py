"""Convierte el JSON `analisis` de la API en markdown legible."""


def marca_tiempo(segundo: float | int | None) -> str:
    total = int(segundo or 0)
    h, resto = divmod(total, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _lista(valor) -> list:
    return valor if isinstance(valor, list) else []


def _seccion(titulo: str, items: list, *, numerada: bool = False) -> list[str]:
    items = [str(i) for i in items if i is not None and str(i).strip()]
    if not items:
        return []
    lineas = [f"## {titulo}"]
    lineas += [f"{n}. {i}" if numerada else f"- {i}" for n, i in enumerate(items, 1)]
    return lineas + [""]


def analisis_a_markdown(
    analisis: dict,
    *,
    titulo: str,
    video_id: str,
    objetivo: str | None,
    verificacion_texto: str,
) -> str:
    analisis = analisis or {}
    out = [f"# {titulo}", ""]
    if objetivo:
        out += [f"**Objetivo:** {objetivo}", ""]
    out += ["## Resumen", analisis.get("resumen") or "", "", "## Puntos clave"]
    capitulo_actual = None
    for p in _lista(analisis.get("puntos_clave")):
        if not isinstance(p, dict):
            continue
        capitulo = p.get("capitulo") or ""
        if capitulo and capitulo != capitulo_actual:
            out += ["", f"### {capitulo}"]
            capitulo_actual = capitulo
        try:
            segundo = int(p.get("segundo") or 0)
        except (TypeError, ValueError):
            segundo = 0
        enlace = p.get("enlace") or f"https://youtu.be/{video_id}?t={segundo}"
        out.append(f"- [{marca_tiempo(segundo)}]({enlace}) {p.get('texto') or ''}")
    out.append("")
    out += _seccion("Notas sobre la transcripción", _lista(analisis.get("notas_transcripcion")))
    entidades = analisis.get("entidades")
    if isinstance(entidades, dict):
        for categoria, valores in entidades.items():
            nombre = str(categoria).replace("_", " ").capitalize()
            out += _seccion(nombre, _lista(valores))
    out += _seccion("Pasos", _lista(analisis.get("pasos")), numerada=True)
    out += _seccion("Para verificar", _lista(analisis.get("afirmaciones_a_verificar")))
    out += ["## Verificación", verificacion_texto, ""]
    return "\n".join(out)
