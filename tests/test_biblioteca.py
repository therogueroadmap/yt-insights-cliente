import re

import pytest

from yti_cliente.cola import Cola
from yti_cliente.proyectos import Proyectos
from yti_cliente.proyectos_vista import regenerar_vistas
from yti_cliente.biblioteca import (
    Biblioteca,
    front_matter,
    leer_front_matter,
    normalizar_objetivo,
    slug,
    texto_verificacion,
)


def front_t(**k):
    base = {
        "video_id": "VID00000001",
        "titulo": "Cómo migrar",
        "canal": "Canal X",
        "url": "https://youtu.be/VID00000001",
        "duracion": 600,
        "fuente": "captions",
        "fuente_testigo": "cliente",
        "verificacion": "verificado",
        "coincidencia_pct": 87,
        "obtenido": "2026-11-18T10:00:00Z",
        "trabajo": "job-1",
    }
    base.update(k)
    return base


def front_a(**k):
    base = {
        "video_id": "VID00000001",
        "objetivo": None,
        "modelo": "m",
        "obtenido": "2026-11-18T10:00:00Z",
        "costo_usd": 0.01,
        "verificacion": "verificado",
        "coincidencia_pct": 87,
        "trabajo": "job-1",
    }
    base.update(k)
    return base


@pytest.fixture
def bib(tmp_path):
    return Biblioteca(tmp_path)


def test_front_matter_ida_vuelta():
    datos = {
        "a": "x: y",
        "b": 'di "hola"',
        "c": "l1\nl2",
        "d": None,
        "e": 5,
        "f": 0.25,
        "g": "ñandú",
    }
    texto = front_matter(datos) + "\ncuerpo\n"
    leidos, cuerpo = leer_front_matter(texto)
    assert leidos == datos
    assert cuerpo == "cuerpo\n"


def test_leer_front_matter_sin_cabecera():
    assert leer_front_matter("hola") == ({}, "hola")


def test_normalizar_objetivo():
    assert normalizar_objetivo("  Migrar   A Microservicios ") == "migrar a microservicios"
    assert normalizar_objetivo(None) == ""
    assert normalizar_objetivo("   ") == ""


def test_texto_verificacion():
    assert texto_verificacion("verificado", 87) == "verificado 87 %"
    assert texto_verificacion("verificado", None) == "verificado"
    assert texto_verificacion("sin_verificacion", None) == "sin verificación"
    assert texto_verificacion("no_aplica", None) == "no aplica"
    assert texto_verificacion(None, None) == "—"


@pytest.mark.parametrize("titulo", ["🎉🎉", "日本語", "con: ?*<>|", "a" * 300, "Cómo migrar"])
def test_slug_titulo_raro(bib, titulo):
    s = slug(titulo, 40)
    assert re.fullmatch(r"[a-z0-9-]*", s)
    assert len(s) <= 40
    assert not s.startswith("-") and not s.endswith("-")
    carpeta = bib.carpeta_video("VID00000001", titulo)
    carpeta.mkdir(parents=True)
    assert carpeta.is_dir()
    if not s:
        assert carpeta == bib.raiz / "videos" / "VID00000001"


def test_carpeta_reutilizada(bib):
    (bib.raiz / "videos" / "ID-titulo-viejo").mkdir(parents=True)
    assert bib.carpeta_video("ID", "Título nuevo") == bib.raiz / "videos" / "ID-titulo-viejo"


@pytest.mark.parametrize(
    "existente,nueva,escribe",
    [
        ("verificado", "sin_verificacion", False),
        ("sin_verificacion", "verificado", True),
        ("verificado", "verificado", True),
        (None, "verificado", True),
        (None, "sin_verificacion", True),
        ("no_aplica", "sin_verificacion", True),
        ("sin_verificacion", "no_aplica", False),
    ],
)
def test_transcripcion_r14(bib, existente, nueva, escribe):
    assert bib.escribir_transcripcion("V1", "T", front_t(verificacion=existente), "viejo") is True
    assert bib.escribir_transcripcion("V1", "T", front_t(verificacion=nueva), "nuevo") is escribe
    texto = bib.tiene_transcripcion("V1").read_text(encoding="utf-8")
    assert ("nuevo" in texto) is escribe


def test_analisis_general_y_objetivo(bib):
    p = bib.escribir_analisis("V1", "T", None, front_a(), "x")
    assert p.name == "analisis-general.md"
    p1 = bib.escribir_analisis("V1", "T", "Migrar a microservicios", front_a(objetivo="Migrar a microservicios"), "uno")
    assert p1.name == "analisis-migrar-a-microservicios.md"
    p2 = bib.escribir_analisis("V1", "T", " migrar a  MICROSERVICIOS", front_a(objetivo=" migrar a  MICROSERVICIOS"), "dos")
    assert p2 == p1
    assert "dos" in p1.read_text(encoding="utf-8")
    assert len(list(p1.parent.glob("analisis-*.md"))) == 2
    assert bib.buscar_analisis("V1", "MIGRAR a microservicios") == p1


def test_colision_slug(bib):
    a = bib.escribir_analisis("V1", "T", "C++", front_a(objetivo="C++"), "a")
    b = bib.escribir_analisis("V1", "T", "C#", front_a(objetivo="C#"), "b")
    assert a != b
    assert a.name == "analisis-c.md"
    assert re.fullmatch(r"analisis-c-[0-9a-f]{6}\.md", b.name)
    assert bib.buscar_analisis("V1", "c#") == b
    assert bib.buscar_analisis("V1", "C++") == a


def _dos_videos(bib):
    bib.escribir_transcripcion(
        "V1",
        "A | B",
        front_t(video_id="V1", titulo="A | B", verificacion=None, coincidencia_pct=None, obtenido="2026-11-01T00:00:00Z"),
        "t1",
    )
    bib.escribir_transcripcion(
        "V2", "Nuevo", front_t(video_id="V2", titulo="Nuevo", obtenido="2026-11-10T00:00:00Z"), "t2"
    )
    bib.escribir_analisis("V2", "Nuevo", "Migrar", front_a(video_id="V2", objetivo="Migrar"), "an")


def test_indice(bib):
    _dos_videos(bib)
    ruta = bib.regenerar_indice()
    assert ruta == bib.raiz / "indice.md"
    texto = ruta.read_text(encoding="utf-8")
    assert "| Video | Título | Fecha | Objetivos | Verificación |" in texto
    assert "A \\| B" in texto
    assert "—" in texto
    assert texto.index("V2") < texto.index("V1")
    assert "solo transcripción" in texto
    assert "verificado 87 %" in texto
    assert "(videos/V2-nuevo/analisis-migrar.md)" in texto


def test_proyecto(bib):
    _dos_videos(bib)
    proyectos = Proyectos(bib, Cola(bib.raiz))
    proyectos.crear("Arquitectura")
    proyectos.agregar_video("Arquitectura", "V2")
    proyectos.agregar_video("Arquitectura", "V2")
    regenerar_vistas(proyectos)
    texto = (bib.raiz / "proyectos" / "arquitectura.md").read_text(encoding="utf-8")
    assert texto.count("](../videos/V2-nuevo/analisis-migrar.md)") == 1
    assert not hasattr(Biblioteca, "agregar_a_proyecto")


def test_listar_buscar_sin_acentos(bib):
    bib.escribir_transcripcion("V1", "Migración", front_t(video_id="V1", titulo="Migración", canal="Otro"), "t")
    bib.escribir_transcripcion("V2", "Nada", front_t(video_id="V2", titulo="Nada", canal="Mi Canal"), "t")
    assert [e.video_id for e in bib.listar(buscar="migracion")] == ["V1"]
    assert [e.video_id for e in bib.listar(buscar="CANAL")] == ["V2"]
    assert len(bib.listar()) == 2
    assert "Migración" in bib.formatear_lista(bib.listar())


def test_resolver_video(bib):
    _dos_videos(bib)
    assert [e.video_id for e in bib.resolver_video("V2")] == ["V2"]
    assert [e.video_id for e in bib.resolver_video("nuev")] == ["V2"]
    assert bib.resolver_video("zzz") == []


PIE = "Otros archivos de este video:"


def test_leer_resultado(bib):
    bib.escribir_transcripcion("V1", "Solo trans", front_t(video_id="V1", titulo="Solo trans"), "TEXTO_T1")
    r = bib.leer_resultado("V1")
    assert "TEXTO_T1" in r and PIE in r.splitlines()[-1]

    bib.escribir_transcripcion("V2", "Uno", front_t(video_id="V2", titulo="Uno"), "TEXTO_T2")
    bib.escribir_analisis("V2", "Uno", "Migrar", front_a(video_id="V2", objetivo="Migrar"), "AN_MIGRAR")
    assert "TEXTO_T2" in bib.leer_resultado("V2", "ignorado", contenido="transcripcion")
    r = bib.leer_resultado("V2")
    assert "AN_MIGRAR" in r and PIE in r.splitlines()[-1]
    assert "AN_MIGRAR" in bib.leer_resultado("V2", "migrar")

    bib.escribir_analisis("V2", "Uno", None, front_a(video_id="V2"), "AN_GENERAL")
    assert "AN_GENERAL" in bib.leer_resultado("V2")

    bib.escribir_transcripcion("V3", "Varios", front_t(video_id="V3", titulo="Varios"), "t")
    bib.escribir_analisis("V3", "Varios", "Uno", front_a(video_id="V3", objetivo="Uno"), "AN_UNO")
    bib.escribir_analisis("V3", "Varios", "Dos", front_a(video_id="V3", objetivo="Dos"), "AN_DOS")
    r = bib.leer_resultado("V3")
    assert "AN_UNO" not in r and "AN_DOS" not in r
    assert "Uno" in r and "Dos" in r and PIE in r.splitlines()[-1]

    r = bib.leer_resultado("V3", "inexistente")
    assert "Uno" in r and "Dos" in r

    r = bib.leer_resultado("o")  # ambiguo: coincide con el título de varios videos
    assert "V1" in r and "V2" in r and "V3" in r
    assert PIE not in r

    r = bib.leer_resultado("V2", contenido="otro")
    assert "analisis" in r and "transcripcion" in r

    assert "zzz" in bib.leer_resultado("zzz")


def test_leer_resultado_objetivo_general(bib):
    bib.escribir_transcripcion("V3", "Tres", front_t(video_id="V3", titulo="Tres"), "T3")
    bib.escribir_analisis("V3", "Tres", "Migrar", front_a(video_id="V3", objetivo="Migrar"), "AN_MIGRAR")
    bib.escribir_analisis("V3", "Tres", None, front_a(video_id="V3"), "AN_GENERAL")
    r = bib.leer_resultado("V3", "general")
    assert "AN_GENERAL" in r and "No hay un análisis" not in r
    assert "AN_GENERAL" in bib.leer_resultado("V3", "General")
    assert "No hay un análisis" in bib.leer_resultado("V3", "otro")
