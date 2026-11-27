from pathlib import Path

from yti_cliente.limites import MAX_NOTA
from yti_cliente.proyectos import AVISO_VISTA
from yti_cliente.proyectos_migracion import migrar_antiguos


def _viejo(proyectos, nombre_archivo, texto):
    proyectos.dir_vistas.mkdir(parents=True, exist_ok=True)
    (proyectos.dir_vistas / f"{nombre_archivo}.md").write_text(texto, encoding="utf-8")


def _carpeta(proyectos, nombre, transcripcion=None):
    d = proyectos.biblioteca.raiz / "videos" / nombre
    d.mkdir(parents=True, exist_ok=True)
    if transcripcion is not None:
        (d / "transcripcion.md").write_text(transcripcion, encoding="utf-8")
    return d


def test_migra_nombre_videos_y_nota(proyectos):
    _carpeta(proyectos, "dQw4w9WgXcQ-como-migrar", "---\nvideo_id: dQw4w9WgXcQ\n---\n\ntexto\n")
    _viejo(proyectos, "arquitectura",
           "# Arquitectura\n\nIdeas sueltas\n\n- [Cómo migrar](../videos/dQw4w9WgXcQ-como-migrar/)\n")
    assert migrar_antiguos(proyectos) == []
    p = proyectos.resolver("Arquitectura")
    assert [v["video_id"] for v in p.videos] == ["dQw4w9WgXcQ"]
    assert [(n["origen"], n["texto"]) for n in p.notas] == [("importado", "Ideas sueltas")]
    assert not (proyectos.dir_vistas / "arquitectura.md").exists()
    assert (proyectos.dir_importados / "arquitectura.md").read_text(encoding="utf-8").startswith("# Arquitectura")


def test_video_id_sale_de_un_analisis_si_no_hay_transcripcion(proyectos):
    d = _carpeta(proyectos, "titulo-sin-id")
    (d / "analisis-x.md").write_text("---\nvideo_id: dQw4w9WgXcQ\n---\n\ncuerpo\n", encoding="utf-8")
    _viejo(proyectos, "a", "# A\n- [x](../videos/titulo-sin-id/)\n")
    assert migrar_antiguos(proyectos) == []
    assert [v["video_id"] for v in proyectos.resolver("A").videos] == ["dQw4w9WgXcQ"]


def test_video_id_con_guion_no_se_parte(proyectos):
    _carpeta(proyectos, "a-b_c123456-titulo-largo")
    _viejo(proyectos, "g", "# G\n- [x](../videos/a-b_c123456-titulo-largo/)\n")
    migrar_antiguos(proyectos)
    assert [v["video_id"] for v in proyectos.resolver("G").videos] == ["a-b_c123456"]


def test_enlace_a_carpeta_inexistente_invalida_se_omite_con_constancia(proyectos):
    _viejo(proyectos, "h", "# H\n- [x](../videos/corto/)\n")
    assert migrar_antiguos(proyectos) == []
    p = proyectos.resolver("H")
    assert p.videos == []
    assert len(p.notas) == 1 and p.notas[0]["origen"] == "importado" and "corto" in p.notas[0]["texto"]


def test_slug_ocupado_importa_con_sufijo(proyectos):
    proyectos.crear("Otro")
    _viejo(proyectos, "otro", "# Distinto\n")
    assert migrar_antiguos(proyectos) == [] and (proyectos.dir_vistas / "otro.md").exists()
    _viejo(proyectos, "arq", "# Otro\n")
    migrar_antiguos(proyectos)
    assert proyectos.resolver("Otro (importado)").nombre == "Otro (importado)"
    _viejo(proyectos, "arq2", "# Otro\n")
    migrar_antiguos(proyectos)
    assert proyectos.resolver("Otro (importado 2)").nombre == "Otro (importado 2)"


def test_sin_encabezado_usa_nombre_de_archivo(proyectos):
    _viejo(proyectos, "mi-tema", "- [x](../videos/dQw4w9WgXcQ-x/)\n")
    migrar_antiguos(proyectos)
    assert proyectos.resolver("mi-tema").nombre == "mi-tema"


def test_no_migra_vistas_generadas_ni_es_repetible(proyectos):
    _viejo(proyectos, "gen", f"{AVISO_VISTA}\n\n# Gen\n")
    assert migrar_antiguos(proyectos) == [] and proyectos.listar() == []
    _viejo(proyectos, "viejo", "# Viejo\n")
    migrar_antiguos(proyectos)
    assert migrar_antiguos(proyectos) == [] and len(proyectos.listar()) == 1


def test_json_ilegible_deja_el_md_intacto_y_avisa(proyectos):
    proyectos.dir_json.mkdir(parents=True, exist_ok=True)
    (proyectos.dir_json / "p-deadbeef.json").write_text("{roto", encoding="utf-8")
    _viejo(proyectos, "viejo", "# Viejo\n")
    avisos = migrar_antiguos(proyectos)
    assert len(avisos) == 1 and "p-deadbeef.json" in avisos[0]
    assert (proyectos.dir_vistas / "viejo.md").is_file()


def test_nota_larga_se_parte(proyectos):
    _viejo(proyectos, "largo", "# Largo\n" + "x" * 9000 + "\n")
    assert migrar_antiguos(proyectos) == []
    notas = proyectos.resolver("Largo").notas
    assert len(notas) == 3 and all(len(n["texto"]) <= MAX_NOTA for n in notas)
    assert "".join(n["texto"] for n in notas) == "x" * 9000


def test_destino_importado_existente_usa_sufijo(proyectos):
    proyectos.dir_importados.mkdir(parents=True)
    (proyectos.dir_importados / "viejo.md").write_text("previo", encoding="utf-8")
    _viejo(proyectos, "viejo", "# Viejo\n")
    assert migrar_antiguos(proyectos) == []
    assert (proyectos.dir_importados / "viejo.md").read_text(encoding="utf-8") == "previo"
    assert (proyectos.dir_importados / "viejo-2.md").read_text(encoding="utf-8").startswith("# Viejo")


def test_fallo_tras_crear_no_borra_el_md_original(proyectos, monkeypatch):
    contenido = "# Arquitectura\n\nIdeas sueltas\n"
    _viejo(proyectos, "arquitectura", contenido)
    original = (proyectos.dir_vistas / "arquitectura.md").read_bytes()

    def falla(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(type(proyectos), "anotar", falla)
    avisos = migrar_antiguos(proyectos)
    assert len(avisos) == 1 and "disco lleno" in avisos[0]
    assert (proyectos.dir_vistas / "arquitectura.md").read_bytes() == original
    assert list(proyectos.dir_json.glob("*.json")) == []


def test_fallo_en_la_limpieza_no_enmascara_el_error_original(proyectos, monkeypatch, caplog):
    _viejo(proyectos, "arquitectura", "# Arquitectura\n\nIdeas\n")

    def falla(*args, **kwargs):
        raise OSError("disco lleno")

    def unlink_bloqueado(self, missing_ok=False):
        raise OSError("bloqueado")

    monkeypatch.setattr(type(proyectos), "anotar", falla)
    monkeypatch.setattr(Path, "unlink", unlink_bloqueado)
    avisos = migrar_antiguos(proyectos)
    assert len(avisos) == 1 and "disco lleno" in avisos[0]
    assert "bloqueado" in caplog.text
    monkeypatch.undo()
    assert (proyectos.dir_vistas / "arquitectura.md").is_file()
