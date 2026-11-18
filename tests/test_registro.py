import logging

import pytest

from yti_cliente.registro import redactar, configurar_registro

CLAVE_G = "AIza" + "x" * 30
CLAVE_A = "sk-ant-" + "y" * 30


@pytest.fixture(autouse=True)
def limpiar_handlers():
    yield
    raiz = logging.getLogger()
    for h in list(raiz.handlers):
        if getattr(h, "_yti", False):
            raiz.removeHandler(h)
            h.close()


def test_redacta_valores_y_cabeceras():
    t = redactar(f"k={CLAVE_G} X-API-Key: yti_otro X-Anthropic-Key={CLAVE_A}", [CLAVE_G, CLAVE_A])
    assert CLAVE_G not in t and CLAVE_A not in t and "yti_otro" not in t and t.count("***") == 3


def test_filtro_en_log_con_args(tmp_path):
    ruta = configurar_registro([CLAVE_G], dir_logs=tmp_path)
    logging.getLogger("yti_cliente.x").warning("fallo con %s", CLAVE_G)
    for h in logging.getLogger().handlers:
        h.flush()
    assert CLAVE_G not in ruta.read_text(encoding="utf-8") and "***" in ruta.read_text(encoding="utf-8")


def test_registro_mcp_sin_stdout(tmp_path, capsys):
    configurar_registro([], modo_mcp=True, dir_logs=tmp_path)
    logging.getLogger("yti_cliente").error("hola")
    assert capsys.readouterr().out == ""


def test_idempotente(tmp_path):
    configurar_registro([CLAVE_G], dir_logs=tmp_path)
    configurar_registro([CLAVE_G], dir_logs=tmp_path)
    propios = [h for h in logging.getLogger().handlers if getattr(h, "_yti", False)]
    assert len(propios) == 2  # archivo + stderr


def test_ruta_por_defecto_usa_platformdirs(tmp_path):
    ruta = configurar_registro([])
    assert ruta == tmp_path / "log" / "yti.log"
