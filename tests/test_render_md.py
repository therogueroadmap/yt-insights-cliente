import pytest

from yti_cliente.render_md import analisis_a_markdown, marca_tiempo

A = {"resumen": "R", "puntos_clave": [{"capitulo": "Intro", "texto": "uno", "segundo": 5},
     {"capitulo": "Intro", "texto": "dos", "segundo": 3725, "enlace": "https://x/y"},
     {"capitulo": "", "texto": "tres", "segundo": 70}],
     "notas_transcripcion": [], "entidades": {"herramientas": ["uv"], "personas": []},
     "pasos": ["a", "b"], "afirmaciones_a_verificar": ["c"]}


def test_estructura():
    md = analisis_a_markdown(A, titulo="T", video_id="ID", objetivo="migrar", verificacion_texto="verificado 87 %")
    assert md.startswith("# T\n") and "**Objetivo:** migrar" in md
    assert md.count("### Intro") == 1 and "- [00:05](https://youtu.be/ID?t=5) uno" in md
    assert "- [1:02:05](https://x/y) dos" in md and "## Herramientas\n- uv" in md and "## Personas" not in md
    assert "1. a\n2. b" in md and "## Para verificar\n- c" in md and "## Notas sobre la transcripción" not in md
    assert md.rstrip().endswith("## Verificación\nverificado 87 %")


def test_sin_objetivo():
    assert "Objetivo" not in analisis_a_markdown(A, titulo="T", video_id="ID", objetivo=None, verificacion_texto="—")


def test_analisis_con_nulos():
    md = analisis_a_markdown({"resumen": None, "puntos_clave": [{"texto": "x"}], "entidades": None},
                             titulo="T", video_id="ID", objetivo=None, verificacion_texto="—")
    assert "- [00:00](https://youtu.be/ID?t=0) x" in md
    assert md.rstrip().endswith("## Verificación\n—")


def test_analisis_vacio():
    md = analisis_a_markdown({}, titulo="T", video_id="ID", objetivo=None, verificacion_texto="—")
    assert "## Resumen" in md and "## Puntos clave" in md


def test_notas_y_pasos_none():
    md = analisis_a_markdown({"notas_transcripcion": ["n"], "pasos": None, "puntos_clave": None},
                             titulo="T", video_id="ID", objetivo=None, verificacion_texto="v")
    assert "## Notas sobre la transcripción\n- n" in md and "## Pasos" not in md


@pytest.mark.parametrize("s,esp", [(0, "00:00"), (59.9, "00:59"), (3599, "59:59"), (3600, "1:00:00"), (None, "00:00")])
def test_marca_tiempo(s, esp):
    assert marca_tiempo(s) == esp
