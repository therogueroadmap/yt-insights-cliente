import json
import re

import pytest

from yti_cliente.errores import ErrorLocal
from yti_cliente.proyectos import slug_proyecto


def test_crear_escribe_json_v1(proyectos):
    p = proyectos.crear("Agentes de IA", "Comparar enfoques")
    datos = json.loads((proyectos.dir_json / f"{p.id}.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"p-[0-9a-f]{8}", p.id)
    assert datos["version"] == 1 and datos["nombre"] == "Agentes de IA"
    assert datos["objetivo"] == "Comparar enfoques" and datos["notas"] == [] and datos["videos"] == []
    assert datos["creado"] == datos["actualizado"] == "2026-11-26T15:00:00Z"

def test_resolver_por_nombre_y_por_id(proyectos):
    p = proyectos.crear("Agentes de IA")
    assert proyectos.resolver("agentes  de ia").id == p.id      # compara por slug
    assert proyectos.resolver(p.id).id == p.id
    with pytest.raises(ErrorLocal) as e: proyectos.resolver("otro")
    assert e.value.codigo == "PROYECTO_NO_EXISTE"

def test_ref_con_forma_de_id_inexistente_no_crea(proyectos):
    with pytest.raises(ErrorLocal) as e: proyectos.resolver_o_crear("p-00000000")
    assert e.value.codigo == "PROYECTO_NO_EXISTE" and proyectos.listar() == []

def test_resolver_o_crear(proyectos):
    p, creado = proyectos.resolver_o_crear("Nuevo")
    q, creado2 = proyectos.resolver_o_crear("nuevo")
    assert (creado, creado2, q.id) == (True, False, p.id)

@pytest.mark.parametrize("nombre,esperado", [("Con", "con-p"), ("COM1", "com1-p"), ("lpt9", "lpt9-p"),
    ("!!!", "proyecto"), ("Ñandú Año", "nandu-ano"), ("x" * 80, "x" * 50)])
def test_slug_proyecto(nombre, esperado):
    assert slug_proyecto(nombre) == esperado

@pytest.mark.parametrize("nombre", ["", "   ", "x" * 101, "p-3fa9c012"])
def test_nombre_invalido(proyectos, nombre):
    with pytest.raises(ErrorLocal) as e: proyectos.crear(nombre)
    assert e.value.codigo == "PROYECTO_NOMBRE_INVALIDO"

def test_nombre_de_100_caracteres_vale(proyectos):
    assert proyectos.crear("x" * 100).nombre == "x" * 100

def test_slug_duplicado(proyectos):
    proyectos.crear("Agentes de IA")
    with pytest.raises(ErrorLocal) as e: proyectos.crear("agentes de ia!")
    assert e.value.codigo == "PROYECTO_YA_EXISTE"

def test_objetivo_largo(proyectos):
    with pytest.raises(ErrorLocal) as e: proyectos.crear("A", "x" * 1001)
    assert e.value.codigo == "OBJETIVO_LARGO"

def test_agregar_idempotente_y_quitar(proyectos):
    proyectos.crear("A")
    assert proyectos.agregar_video("A", "V1") is True
    assert proyectos.agregar_video("A", "V1") is False
    assert [v["video_id"] for v in proyectos.resolver("A").videos] == ["V1"]
    proyectos.quitar_video("A", "V1")
    with pytest.raises(ErrorLocal) as e: proyectos.quitar_video("A", "V1")
    assert e.value.codigo == "VIDEO_NO_EN_PROYECTO"

def test_editar_renombra_cambia_y_borra_objetivo_y_vista_vieja(proyectos):
    p = proyectos.crear("Viejo", "obj")
    vieja = proyectos.ruta_vista(p); vieja.parent.mkdir(parents=True); vieja.write_text("x", encoding="utf-8")
    q = proyectos.editar("Viejo", nombre="Nuevo", objetivo="")
    assert (q.id, q.nombre, q.objetivo) == (p.id, "Nuevo", None)
    assert not vieja.exists()
    assert proyectos.editar("Nuevo").objetivo is None            # SIN_CAMBIO no toca nada

def test_editar_a_nombre_existente(proyectos):
    proyectos.crear("A"); proyectos.crear("B")
    with pytest.raises(ErrorLocal) as e: proyectos.editar("B", nombre="a")
    assert e.value.codigo == "PROYECTO_YA_EXISTE"

def test_anotar_topes_y_origen(proyectos):
    proyectos.crear("A")
    p = proyectos.anotar("A", "  hola ", "mcp")
    assert p.notas == [{"fecha": "2026-11-26T15:00:00Z", "texto": "hola", "origen": "mcp"}]
    with pytest.raises(ErrorLocal) as e: proyectos.anotar("A", "x" * 4001, "cli")
    assert e.value.codigo == "NOTA_LARGA"
    for vacio in ("", "   "):                                    # Review Focus 2
        with pytest.raises(ErrorLocal) as e: proyectos.anotar("A", vacio, "cli")
        assert e.value.codigo == "NOTA_VACIA"

def test_borrar_quita_json_y_vista_no_videos(proyectos):
    p = proyectos.crear("A")
    proyectos.agregar_video("A", "V1")
    carpeta = proyectos.biblioteca.raiz / "videos" / "V1-x"; carpeta.mkdir(parents=True)
    vista = proyectos.ruta_vista(p); vista.parent.mkdir(parents=True); vista.write_text("v", encoding="utf-8")
    proyectos.borrar("A")
    assert not (proyectos.dir_json / f"{p.id}.json").exists() and not vista.exists() and carpeta.is_dir()

def test_version_mayor_rechazada_sin_tocar(proyectos):
    p = proyectos.crear("A")
    ruta = proyectos.dir_json / f"{p.id}.json"
    datos = json.loads(ruta.read_text(encoding="utf-8")); datos["version"] = 2
    ruta.write_text(json.dumps(datos), encoding="utf-8"); antes = ruta.read_bytes()
    with pytest.raises(ErrorLocal) as e: proyectos.listar()
    assert e.value.codigo == "PROYECTO_VERSION_NUEVA" and "actualiza" in e.value.mensaje
    assert ruta.read_bytes() == antes

def test_json_corrupto_se_omite_y_no_se_sobreescribe(proyectos):
    ok = proyectos.crear("Bueno")
    mal = proyectos.dir_json / "p-deadbeef.json"; mal.write_text("{no es json", encoding="utf-8")
    assert [p.id for p in proyectos.listar()] == [ok.id]
    assert proyectos.ilegibles() == ["p-deadbeef.json"]
    with pytest.raises(ErrorLocal) as e: proyectos.crear("Otro")        # unicidad no garantizable
    assert e.value.codigo == "PROYECTO_ILEGIBLE" and "p-deadbeef.json" in e.value.mensaje
    assert mal.read_text(encoding="utf-8") == "{no es json"
    assert proyectos.resolver("Bueno").id == ok.id                      # lecturas siguen funcionando

def test_json_con_nombre_distinto_al_id_es_ilegible(proyectos):      # Review Focus 1
    p = proyectos.crear("A")
    copia = proyectos.dir_json / f"{p.id} (conflicto).json"
    copia.write_text((proyectos.dir_json / f"{p.id}.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert [x.id for x in proyectos.listar()] == [p.id]
    assert proyectos.ilegibles() == [copia.name]

def test_proyecto_pedido_ilegible_da_ilegible_no_inexistente(proyectos):
    (proyectos.dir_json).mkdir(parents=True)
    (proyectos.dir_json / "p-deadbeef.json").write_text("{", encoding="utf-8")
    with pytest.raises(ErrorLocal) as e: proyectos.resolver("p-deadbeef")
    assert e.value.codigo == "PROYECTO_ILEGIBLE"
