import json
import logging
import re
from datetime import datetime, timedelta, timezone

import pytest

from yti_cliente.cola import Cola, EsquemaMasNuevo, desde_iso, iso


class Reloj:
    def __init__(self):
        self.t = datetime(2026, 11, 18, 14, 25, 30, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def avanzar(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def reloj():
    return Reloj()


@pytest.fixture
def par(tmp_path, reloj):
    return Cola(tmp_path, reloj=reloj, id_proceso="A"), Cola(tmp_path, reloj=reloj, id_proceso="B")


def _datos(**extra):
    d = {"url": "https://youtu.be/XXXXXXXXXXX", "video_id": "XXXXXXXXXXX", "modo": "analisis",
         "objetivo": "Migrar", "objetivo_normalizado": "migrar"}
    d.update(extra)
    return d


def _por_enviar(cola, **extra):
    t = cola.crear(_datos(**extra))
    return cola.liberar(t, estado="por_enviar")


def test_iso_roundtrip():
    dt = datetime(2026, 11, 18, 14, 25, 30, tzinfo=timezone.utc)
    assert iso(dt) == "2026-11-18T14:25:30Z"
    assert desde_iso("2026-11-18T14:25:30Z") == dt


def test_crear_campos_y_toma(tmp_path, par):
    a, _ = par
    t = a.crear(_datos())
    assert t["estado"] == "enviando"
    assert t["tomado_por"] == "A"
    assert t["tomado_en"] == "2026-11-18T14:25:30Z"
    assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{4}$", t["id_local"])
    assert t["id_local"].startswith("20261118-142530-")
    assert (tmp_path / ".yti" / "trabajos" / f"{t['id_local']}.json").exists()
    assert (tmp_path / ".yti" / "version").read_text().strip() == "1"
    assert t["version"] == 1 and t["reenvios_claves"] == 0 and t["id_remoto"] is None


def test_id_proceso_por_defecto(tmp_path):
    assert Cola(tmp_path).id_proceso.count(":") == 2


def test_toma_exclusiva_dos_procesos(par):
    a, b = par
    _por_enviar(a)
    assert len(a.tomar_para_envio()) == 1
    assert len(b.tomar_para_envio()) == 0


def test_toma_vencida(par, reloj):
    a, b = par
    t = a.crear(_datos())
    reloj.avanzar(minutes=9)
    assert b.tomar_para_envio() == []
    reloj.avanzar(minutes=2)
    tomados = b.tomar_para_envio()
    assert len(tomados) == 1
    assert b.leer(t["id_local"])["tomado_por"] == "B"


def test_retenido_vence(par, reloj):
    a, b = par
    t = a.crear(_datos())
    a.liberar(t, estado="retenido", enviar_despues=iso(reloj() + timedelta(hours=1)))
    assert b.tomar_para_envio() == []
    assert b.hay_activos() is False
    reloj.avanzar(hours=1)
    assert b.hay_activos() is True
    assert len(b.tomar_para_envio()) == 1


def test_actualizar_si_descarta(par):
    a, b = par
    t = a.crear(_datos())
    a.liberar(t, estado="enviado", id_remoto="r1")
    visto = a.leer(t["id_local"])
    b.actualizar_si(t["id_local"], {"id_remoto": "r1"}, {"id_remoto": "r2"})
    r = a.actualizar_si(t["id_local"], {"id_remoto": visto["id_remoto"], "estado": "enviado"},
                        {"estado": "en_biblioteca"})
    assert r is None
    assert a.leer(t["id_local"])["estado"] == "enviado"
    assert a.leer(t["id_local"])["id_remoto"] == "r2"


def test_reenvios_claves_persiste(tmp_path, par):
    a, _ = par
    t = a.crear(_datos())
    t["reenvios_claves"] = 1
    a.guardar(t)
    assert Cola(tmp_path).leer(t["id_local"])["reenvios_claves"] == 1


def test_buscar_activo(par):
    a, _ = par
    t = _por_enviar(a)
    assert a.buscar_activo("XXXXXXXXXXX", "migrar", "analisis")["id_local"] == t["id_local"]
    assert a.buscar_activo("XXXXXXXXXXX", "otro", "analisis") is None
    assert a.buscar_activo("XXXXXXXXXXX", "", "transcripcion")["id_local"] == t["id_local"]
    for estado in ("error", "perdido", "en_biblioteca"):
        a.liberar(t, estado=estado)
        assert a.buscar_activo("XXXXXXXXXXX", "migrar", "analisis") is None
        assert a.buscar_activo("XXXXXXXXXXX", "", "transcripcion") is None


def test_esquema_mas_nuevo(tmp_path):
    (tmp_path / ".yti").mkdir()
    (tmp_path / ".yti" / "version").write_text("2")
    with pytest.raises(EsquemaMasNuevo):
        Cola(tmp_path).crear(_datos())


def test_listar_ignora_corruptos(tmp_path, par, caplog):
    a, _ = par
    t1 = a.crear(_datos())
    carpeta = tmp_path / ".yti" / "trabajos"
    (carpeta / "20260101-000000-aaaa.json").write_text('{"id_local": "20260101-0', encoding="utf-8")
    (carpeta / "x (conflicto).json").write_text("no es json", encoding="utf-8")
    (carpeta / "lista.json").write_text("[1]", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        lista = a.listar()
    assert [t["id_local"] for t in lista] == [t1["id_local"]]
    assert sum(1 for r in caplog.records if r.levelno == logging.WARNING) >= 3


def test_testigo_guardar_leer_borrar(tmp_path, par):
    a, _ = par
    assert a.leer_testigo("x") is None
    a.guardar_testigo("x", "hola ñ")
    assert (tmp_path / ".yti" / "testigos" / "x.vtt").exists()
    assert a.leer_testigo("x") == "hola ñ"
    a.borrar_testigo("x")
    assert a.leer_testigo("x") is None
    a.borrar_testigo("x")


def test_json_utf8_indentado(tmp_path, par):
    a, _ = par
    t = a.crear(_datos(objetivo="añadir"))
    texto = (tmp_path / ".yti" / "trabajos" / f"{t['id_local']}.json").read_text(encoding="utf-8")
    assert "añadir" in texto and '\n  "' in texto
    assert json.loads(texto)["objetivo"] == "añadir"


def test_listar_omite_copia_de_conflicto_valida(tmp_path, par, caplog):
    a, _ = par
    t = _por_enviar(a)
    carpeta = tmp_path / ".yti" / "trabajos"
    copia = carpeta / f"{t['id_local']} (conflicted copy).json"
    copia.write_text((carpeta / f"{t['id_local']}.json").read_text(encoding="utf-8"), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        lista = a.listar()
    assert [x["id_local"] for x in lista] == [t["id_local"]]
    assert any("conflict" in r.getMessage() for r in caplog.records)
    assert len(a.tomar_para_envio()) == 1
