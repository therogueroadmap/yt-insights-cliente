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


def test_filtro_redacta_traza_de_log_exception(tmp_path):
    ruta = configurar_registro([CLAVE_G], dir_logs=tmp_path)
    try:
        raise ValueError(f"explotó con {CLAVE_G}")
    except ValueError:
        logging.getLogger("yti_cliente.x").exception("falló")
    for h in logging.getLogger().handlers:
        h.flush()
    texto = ruta.read_text(encoding="utf-8")
    assert "Traceback" in texto and "ValueError" in texto
    assert CLAVE_G not in texto and "***" in texto


def test_consola_solo_warning_y_archivo_info(tmp_path, capsys):
    ruta = configurar_registro([], dir_logs=tmp_path)
    logging.getLogger("httpx").info("HTTP Request: GET x")
    logging.getLogger("yti_cliente.x").warning("aviso visible")
    for h in logging.getLogger().handlers:
        h.flush()
    err = capsys.readouterr().err
    assert "HTTP Request" not in err and "aviso visible" in err
    assert "HTTP Request" in ruta.read_text(encoding="utf-8")


def _vaciar(logger_nombre="yti_cliente.ytdlp"):
    for h in logging.getLogger().handlers + logging.getLogger(logger_nombre).handlers:
        h.flush()


def test_ytdlp_no_llega_a_la_consola_pero_si_al_archivo(tmp_path, capsys):
    ruta = configurar_registro([], dir_logs=tmp_path)
    lg = logging.getLogger("yti_cliente.ytdlp")
    lg.warning("[youtube] No supported JavaScript runtime")
    lg.error("ERROR: algo de yt-dlp")
    logging.getLogger("yti_cliente.otro").warning("aviso normal")
    _vaciar()
    err = capsys.readouterr().err
    assert "JavaScript" not in err and "yt-dlp" not in err and "aviso normal" in err
    texto = ruta.read_text(encoding="utf-8")
    assert "No supported JavaScript runtime" in texto and "algo de yt-dlp" in texto
    assert "aviso normal" in texto


def test_ytdlp_no_escribe_en_consola_ni_en_modo_mcp(tmp_path, capsys):
    configurar_registro([], modo_mcp=True, dir_logs=tmp_path)
    logging.getLogger("yti_cliente.ytdlp").warning("[youtube] aviso")
    _vaciar()
    capturado = capsys.readouterr()
    assert capturado.out == "" and "aviso" not in capturado.err


def test_otros_avisos_siguen_en_la_consola(tmp_path, capsys):
    configurar_registro([], dir_logs=tmp_path)
    logging.getLogger("yti_cliente.otro").warning("visible")
    _vaciar()
    assert "visible" in capsys.readouterr().err


def test_ytdlp_redacta_claves_en_el_archivo(tmp_path):
    ruta = configurar_registro([CLAVE_G], dir_logs=tmp_path)
    logging.getLogger("yti_cliente.ytdlp").warning("k=%s", CLAVE_G)
    _vaciar()
    assert CLAVE_G not in ruta.read_text(encoding="utf-8")


def test_idempotente_con_ytdlp(tmp_path):
    configurar_registro([], dir_logs=tmp_path)
    configurar_registro([], dir_logs=tmp_path)
    ruta = tmp_path / "yti.log"
    logging.getLogger("yti_cliente.ytdlp").warning("una vez")
    _vaciar()
    assert ruta.read_text(encoding="utf-8").count("una vez") == 1
