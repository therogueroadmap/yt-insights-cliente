import os
import stat
from pathlib import Path

import pytest

from yti_cliente.config import (
    ErrorConfig,
    cargar_config,
    claves_cargadas,
    enmascarar,
    guardar_valor,
    validar_clave_proveedor,
    validar_idioma,
    idioma_del_sistema,
    _codigo_desde_locale,
)

CLAVE_G = "AIza" + "x" * 30
CLAVE_A = "sk-ant-" + "y" * 30
CLAVE_Y = "yti_secreta_1234"


def test_entorno_gana_al_archivo(tmp_path):
    ruta = tmp_path / "c.toml"
    guardar_valor("gemini", "A" * 25, ruta)
    cfg = cargar_config(ruta, {"YTI_GEMINI_KEY": CLAVE_G})
    assert cfg.clave_gemini == CLAVE_G and cfg.origen["gemini"] == "entorno"


def test_archivo_se_usa_sin_entorno(tmp_path):
    ruta = tmp_path / "c.toml"
    guardar_valor("gemini", "A" * 25, ruta)
    cfg = cargar_config(ruta, {})
    assert cfg.clave_gemini == "A" * 25 and cfg.origen["gemini"] == "archivo"
    assert cfg.origen["anthropic"] == "ausente"


def test_ignora_variables_genericas(tmp_path):
    cfg = cargar_config(tmp_path / "no.toml", {"GEMINI_API_KEY": CLAVE_G, "ANTHROPIC_API_KEY": CLAVE_A})
    assert cfg.clave_gemini is None and cfg.clave_anthropic is None


def test_defectos(tmp_path):
    cfg = cargar_config(tmp_path / "no.toml", {})
    assert cfg.api_url == "https://yt-insights-api-f8y7.onrender.com"
    assert cfg.biblioteca == Path("~/yt-insights").expanduser() and cfg.origen["api_url"] == "defecto"
    assert cfg.origen["biblioteca"] == "defecto" and cfg.clave_yti is None


def test_api_url_sin_barra_final(tmp_path):
    cfg = cargar_config(tmp_path / "no.toml", {"YTI_API_URL": "http://x.test/"})
    assert cfg.api_url == "http://x.test" and cfg.origen["api_url"] == "entorno"


def test_guardar_conserva_otros_valores(tmp_path):
    ruta = tmp_path / "c.toml"
    guardar_valor("biblioteca", "D:/bib", ruta)
    guardar_valor("anthropic", CLAVE_A, ruta)
    cfg = cargar_config(ruta, {})
    assert cfg.biblioteca == Path("D:/bib") and cfg.clave_anthropic == CLAVE_A


def test_ruta_por_defecto_usa_platformdirs(tmp_path):
    guardar_valor("yt_insights", CLAVE_Y)
    assert cargar_config(entorno={}).clave_yti == CLAVE_Y


@pytest.mark.skipif(os.name == "nt", reason="permisos POSIX")
def test_permisos_0600(tmp_path):
    ruta = tmp_path / "c.toml"
    guardar_valor("gemini", CLAVE_G, ruta)
    assert stat.S_IMODE(ruta.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "v,ok",
    [("a" * 20, True), ("a" * 19, False), ("a" * 513, False), ("abc def" + "a" * 20, False), ("A-b_c.9" * 4, True)],
)
def test_validar_clave_proveedor(v, ok):
    assert validar_clave_proveedor(v) is ok


def test_enmascarar():
    assert enmascarar(CLAVE_Y) == "…1234" and enmascarar(None) == "(no configurada)" and enmascarar("abc") == "****"


def test_claves_cargadas(tmp_path):
    cfg = cargar_config(tmp_path / "no.toml", {"YTI_API_KEY": CLAVE_Y, "YTI_GEMINI_KEY": ""})
    assert claves_cargadas(cfg) == [CLAVE_Y]


def test_campo_desconocido(tmp_path):
    with pytest.raises(ErrorConfig):
        guardar_valor("otra", "x", tmp_path / "c.toml")


def test_toml_ilegible(tmp_path):
    ruta = tmp_path / "c.toml"
    ruta.write_text("esto no [es toml", encoding="utf-8")
    with pytest.raises(ErrorConfig):
        cargar_config(ruta, {})


# --- idioma ---


@pytest.fixture
def sistema_en(monkeypatch):
    monkeypatch.setattr("yti_cliente.config.idioma_del_sistema", lambda: "fr")


def test_idioma_defecto_es_el_del_sistema(tmp_path, sistema_en):
    cfg = cargar_config(tmp_path / "no.toml", {})
    assert cfg.idioma == "fr" and cfg.origen["idioma"] == "defecto"


def test_idioma_prioridad_entorno_archivo_defecto(tmp_path, sistema_en):
    ruta = tmp_path / "c.toml"
    guardar_valor("idioma", "pt", ruta)
    cfg = cargar_config(ruta, {})
    assert cfg.idioma == "pt" and cfg.origen["idioma"] == "archivo"
    cfg = cargar_config(ruta, {"YTI_IDIOMA": "de"})
    assert cfg.idioma == "de" and cfg.origen["idioma"] == "entorno"


@pytest.mark.parametrize("valor", ["es", "spa", "es-419", "pt-BR"])
def test_validar_idioma_valido(valor):
    assert validar_idioma(valor) == valor


@pytest.mark.parametrize("valor", ["", "e", "ES", "espanol", "es_ES", "es-", "es-" + "x" * 20, "e1", "es 419"])
def test_validar_idioma_invalido(valor):
    with pytest.raises(ErrorConfig):
        validar_idioma(valor)


def test_guardar_idioma_invalido_no_escribe(tmp_path):
    ruta = tmp_path / "c.toml"
    with pytest.raises(ErrorConfig):
        guardar_valor("idioma", "ES_es", ruta)
    assert not ruta.exists()


def test_idioma_invalido_en_entorno_o_archivo_es_error(tmp_path):
    with pytest.raises(ErrorConfig):
        cargar_config(tmp_path / "no.toml", {"YTI_IDIOMA": "klingon!"})
    ruta = tmp_path / "c.toml"
    ruta.write_text('idioma = "xx_YY_zz"\n', encoding="utf-8")
    with pytest.raises(ErrorConfig):
        cargar_config(ruta, {})


@pytest.mark.parametrize(
    "nombre, esperado",
    [("es_ES", "es"), ("es-419", "es"), ("en_US.UTF-8", "en"), ("pt-BR", "pt"), ("C", None),
     ("POSIX", None), ("Spanish_Spain", None), ("", None), (None, None)],
)
def test_codigo_desde_locale(nombre, esperado):
    assert _codigo_desde_locale(nombre) == esperado


def test_idioma_del_sistema_windows_usa_api_de_windows(monkeypatch):
    monkeypatch.setattr("yti_cliente.config.sys.platform", "win32")
    monkeypatch.setattr("yti_cliente.config._locale_windows", lambda: "es-419")
    monkeypatch.setattr("yti_cliente.config.locale.getlocale", lambda *a: ("Italian_Italy", "1252"))
    assert idioma_del_sistema() == "es"


def test_idioma_del_sistema_posix_usa_getlocale(monkeypatch):
    monkeypatch.setattr("yti_cliente.config.sys.platform", "linux")
    monkeypatch.setattr("yti_cliente.config.locale.getlocale", lambda *a: ("pt_BR", "UTF-8"))
    assert idioma_del_sistema() == "pt"


def test_idioma_del_sistema_sin_dato_cae_a_es(monkeypatch):
    monkeypatch.setattr("yti_cliente.config.sys.platform", "linux")
    monkeypatch.setattr("yti_cliente.config.locale.getlocale", lambda *a: (None, None))
    for v in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(v, raising=False)
    assert idioma_del_sistema() == "es"


def test_idioma_del_sistema_windows_sin_api_cae_a_es(monkeypatch):
    monkeypatch.setattr("yti_cliente.config.sys.platform", "win32")
    monkeypatch.setattr("yti_cliente.config._locale_windows", lambda: None)
    monkeypatch.setattr("yti_cliente.config.locale.getlocale", lambda *a: ("Spanish_Spain", "1252"))
    for v in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(v, raising=False)
    assert idioma_del_sistema() == "es"
