from yti_cliente.proyectos import AVISO_VISTA
from yti_cliente.proyectos_vista import (
    estado_video, regenerar_vistas, texto_leer_proyecto, texto_vista)


def _trans(proyectos, vid, titulo):
    proyectos.biblioteca.escribir_transcripcion(
        vid, titulo, {"video_id": vid, "titulo": titulo, "obtenido": "2026-11-26T10:00:00Z"}, "texto\n")


def _analisis(proyectos, vid, titulo, objetivo):
    return proyectos.biblioteca.escribir_analisis(
        vid, titulo, objetivo,
        {"video_id": vid, "titulo": titulo, "objetivo": objetivo, "obtenido": "2026-11-26T10:00:00Z"},
        "cuerpo\n")


def _trabajo(proyectos, vid, estado):
    t = proyectos.cola.crear({"video_id": vid, "url": f"https://youtu.be/{vid}"})
    t["estado"] = estado
    proyectos.cola.guardar(t)
    return t


def test_estados(proyectos):
    _trans(proyectos, "V1", "uno")
    _trabajo(proyectos, "V2", "por_enviar")
    _trabajo(proyectos, "V3", "fallido")
    assert [estado_video(proyectos, v) for v in ("V1", "V2", "V3", "V4")] == [
        "en biblioteca", "en cola", "con error", "sin pedir"]


def test_en_cola_gana_a_en_biblioteca(proyectos):
    _trans(proyectos, "V1", "uno")
    _trabajo(proyectos, "V1", "por_enviar")
    assert estado_video(proyectos, "V1") == "en cola"


def _sembrar(proyectos, objetivo="Comparar enfoques"):
    p = proyectos.crear("Agentes de IA", objetivo)
    _trans(proyectos, "V1", "uno")
    _analisis(proyectos, "V1", "uno", "Comparar enfoques")
    _trans(proyectos, "V2", "dos")
    for v in ("V1", "V2", "V3"):
        proyectos.agregar_video("Agentes de IA", v)
    return proyectos.resolver("Agentes de IA")


def test_vista_contenido(proyectos):
    _sembrar(proyectos)
    proyectos.anotar("Agentes de IA", "primera", "cli")
    proyectos.anotar("Agentes de IA", "segunda", "mcp")
    texto = texto_vista(proyectos, proyectos.resolver("Agentes de IA"))
    assert texto.splitlines()[0] == AVISO_VISTA
    assert "# Agentes de IA" in texto and "Comparar enfoques" in texto
    assert texto.index("primera") < texto.index("segunda")
    assert "2026-11-26T15:00:00Z" in texto and "mcp" in texto
    assert "(../videos/V1-uno/analisis-comparar-enfoques.md)" in texto
    assert "(../videos/V2-dos/transcripcion.md)" in texto
    fila_v3 = next(l for l in texto.splitlines() if "V3" in l)
    assert "sin pedir" in fila_v3


def test_enlace_a_otro_analisis_si_no_hay_el_del_objetivo(proyectos):
    proyectos.crear("P", "X")
    _analisis(proyectos, "V1", "uno", None)
    proyectos.agregar_video("P", "V1")
    fila = next(l for l in texto_vista(proyectos, proyectos.resolver("P")).splitlines() if "V1-" in l)
    assert "(../videos/V1-uno/analisis-general.md)" in fila
    assert fila.split("|")[3].strip() == "no"


def test_titulo_con_barra_y_salto_no_rompe_la_tabla(proyectos):
    p = proyectos.crear("P")
    _trans(proyectos, "V1", "A | B\nC")
    proyectos.agregar_video("P", "V1")
    fila = next(l for l in texto_vista(proyectos, proyectos.resolver("P")).splitlines() if "V1-" in l)
    assert "A \\| B C" in fila and fila.count("|") - fila.count("\\|") == 6


def test_proyecto_vacio(proyectos):
    p = proyectos.crear("Vacío")
    assert AVISO_VISTA in texto_vista(proyectos, p)
    assert "# Vacío" in texto_vista(proyectos, p)
    texto = texto_leer_proyecto(proyectos, p)
    assert "# Vacío" in texto and "leer_resultado(" not in texto


def test_regenerar_vistas_escribe_todas(proyectos):
    proyectos.crear("A")
    proyectos.crear("B")
    rutas = regenerar_vistas(proyectos)
    assert sorted(r.name for r in rutas) == ["a.md", "b.md"]
    assert AVISO_VISTA in (proyectos.biblioteca.raiz / "proyectos" / "a.md").read_text(encoding="utf-8")


def test_regenerar_no_pisa_md_que_no_es_vista(proyectos):
    proyectos.crear("A")
    viejo = proyectos.biblioteca.raiz / "proyectos" / "a.md"
    viejo.parent.mkdir(parents=True)
    viejo.write_text("# A\n\n- [x](../videos/V1-x/)\n", encoding="utf-8")
    assert regenerar_vistas(proyectos) == []
    assert viejo.read_text(encoding="utf-8") == "# A\n\n- [x](../videos/V1-x/)\n"


def test_renombrar_y_regenerar_deja_una_sola_vista(proyectos):
    proyectos.crear("Viejo")
    regenerar_vistas(proyectos)
    proyectos.editar("Viejo", nombre="Nuevo")
    regenerar_vistas(proyectos)
    assert sorted(p.name for p in (proyectos.biblioteca.raiz / "proyectos").glob("*.md")) == ["nuevo.md"]


def test_leer_proyecto_punteros(proyectos):
    p = _sembrar(proyectos)
    _analisis(proyectos, "V1", "uno", None)
    texto = texto_leer_proyecto(proyectos, p)
    assert 'leer_resultado(video="V1", objetivo="Comparar enfoques")' in texto
    assert "(objetivo del proyecto)" in texto
    assert 'leer_resultado(video="V2", contenido="transcripcion")' in texto
    assert "solo transcripción" in texto
    assert 'video="V3"' not in texto and "sin pedir" in texto


def test_puntero_usa_el_primer_objetivo_si_falta_el_del_proyecto(proyectos):
    proyectos.crear("P", "X")
    _analisis(proyectos, "V1", "uno", "Y")
    proyectos.agregar_video("P", "V1")
    texto = texto_leer_proyecto(proyectos, proyectos.resolver("P"))
    assert 'leer_resultado(video="V1", objetivo="Y")' in texto
    assert "(objetivo del proyecto)" not in texto
