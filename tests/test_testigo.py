import logging
import os

import pytest
from yt_dlp.networking.exceptions import TransportError
from yt_dlp.utils import DownloadError

from yti_cliente.testigo import (
    TOPE_BYTES,
    SinConexion,
    elegir_pista,
    obtener_testigo,
)

VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHola\n"


class FakeYDL:
    """Simula yt-dlp: extract_info devuelve info, download escribe el VTT."""

    def __init__(self, opts, info, vtt, error_download=None, error_info=None):
        self.opts = opts
        self.info = info
        self.vtt = vtt
        self.error_download = error_download
        self.error_info = error_info

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        if self.error_info:
            raise self.error_info
        return self.info

    def download(self, urls):
        if self.error_download:
            raise self.error_download
        if self.vtt is None:
            return 0
        clave = self.opts["subtitleslangs"][0]
        with open(f"{self.opts['outtmpl']}.{clave}.vtt", "w", encoding="utf-8", newline="") as f:
            f.write(self.vtt)
        return 0


def fabrica(info, vtt=VTT, error_download=None, error_info=None):
    creadas = []

    def f(opts):
        y = FakeYDL(opts, info, vtt, error_download, error_info)
        creadas.append(y)
        return y

    f.creadas = creadas
    return f


INFO_ES = {
    "language": "es",
    "title": "T",
    "channel": "C",
    "duration": 61,
    "subtitles": {"es": []},
}


@pytest.mark.parametrize(
    "info,esperado",
    [
        ({"language": "es", "subtitles": {"es": [], "en": []}, "automatic_captions": {"es-orig": []}}, ("es", "manual")),
        ({"language": "es-419", "subtitles": {"es-ES": []}}, ("es-ES", "manual")),
        ({"language": "es", "subtitles": {"en": []}, "automatic_captions": {"es-orig": [], "en": []}}, ("es-orig", "auto")),
        ({"language": None, "automatic_captions": {"fr": [], "ja-orig": []}}, ("ja-orig", "auto")),
        ({"language": "es", "subtitles": {"live_chat": [], "de": []}, "automatic_captions": {"en": []}}, ("de", "manual")),
        ({"language": "es", "subtitles": {"live_chat": []}, "automatic_captions": {"en": []}}, None),
        ({"language": "es", "automatic_captions": {"es": [], "en": []}}, ("es", "auto")),
    ],
)
def test_elegir_pista(info, esperado):
    assert elegir_pista(info) == esperado


def test_obtenido():
    r = obtener_testigo("u", ydl_factory=fabrica(INFO_ES))
    assert r.estado == "obtenido"
    assert r.idioma == "es"
    assert r.contenido == VTT
    assert r.info.titulo == "T"
    assert r.info.canal == "C"
    assert r.info.duracion_s == 61


def test_canal_usa_uploader():
    info = {k: v for k, v in INFO_ES.items() if k != "channel"}
    info["uploader"] = "U"
    r = obtener_testigo("u", ydl_factory=fabrica(info))
    assert r.info.canal == "U"


def test_idioma_recortado():
    info = {"language": None, "automatic_captions": {"zh-Hans-xxxxxxxxxxxx-orig": []}}
    r = obtener_testigo("u", ydl_factory=fabrica(info))
    assert r.estado == "obtenido"
    assert len(r.idioma) <= 16
    assert "-orig" not in r.idioma


def test_sin_pistas():
    r = obtener_testigo("u", ydl_factory=fabrica({"title": "T", "language": "es"}))
    assert r.estado == "sin_subtitulos"
    assert r.info.titulo == "T"


def test_vtt_vacio():
    r = obtener_testigo("u", ydl_factory=fabrica(INFO_ES, vtt="WEBVTT\n\n"))
    assert r.estado == "sin_subtitulos"
    assert r.info is not None


def test_sin_archivo_descargado():
    r = obtener_testigo("u", ydl_factory=fabrica(INFO_ES, vtt=None))
    assert r.estado == "sin_subtitulos"


def test_vtt_supera_tope():
    cabecera = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n"
    grande = cabecera + "a" * (TOPE_BYTES + 1 - len(cabecera.encode()))
    assert len(grande.encode()) == TOPE_BYTES + 1
    r = obtener_testigo("u", ydl_factory=fabrica(INFO_ES, vtt=grande))
    assert r.estado == "sin_subtitulos"
    assert "tope" in r.motivo
    assert r.contenido is None


def test_error_ytdlp_fallido():
    r = obtener_testigo("u", ydl_factory=fabrica(INFO_ES, error_download=DownloadError("bloqueado")))
    assert r.estado == "fallido"
    assert "bloqueado" in r.motivo
    assert "Traceback" not in r.motivo
    assert "\n" not in r.motivo


def test_error_en_extract_info_fallido():
    r = obtener_testigo("u", ydl_factory=fabrica(INFO_ES, error_info=DownloadError("privado")))
    assert r.estado == "fallido"


def test_sin_conexion():
    err = DownloadError("fallo", exc_info=(ConnectionError, ConnectionError("x"), None))
    with pytest.raises(SinConexion):
        obtener_testigo("u", ydl_factory=fabrica(INFO_ES, error_download=err))


def test_sin_conexion_por_cause_transport():
    err = DownloadError("fallo")
    err.__cause__ = TransportError("caído")
    with pytest.raises(SinConexion):
        obtener_testigo("u", ydl_factory=fabrica(INFO_ES, error_info=err))


def test_sin_conexion_oserror_directo():
    with pytest.raises(SinConexion):
        obtener_testigo("u", ydl_factory=fabrica(INFO_ES, error_info=OSError("red")))


def test_temporal_borrado():
    f = fabrica(INFO_ES)
    obtener_testigo("u", ydl_factory=f)
    outtmpl = f.creadas[-1].opts["outtmpl"]
    assert not os.path.exists(os.path.dirname(outtmpl))


def test_opciones_ytdlp_silenciosas(capsys):
    f = fabrica(INFO_ES)
    obtener_testigo("u", ydl_factory=f)
    assert len(f.creadas) == 2
    for y in f.creadas:
        assert y.opts["quiet"] is True
        assert y.opts["no_warnings"] is True
        assert y.opts["noprogress"] is True
        assert y.opts["logger"] is not None
    d = f.creadas[1].opts
    assert d["skip_download"] is True
    assert d["writesubtitles"] is True
    assert not d.get("writeautomaticsub")
    assert d["subtitleslangs"] == ["es"]
    assert d["subtitlesformat"] == "vtt"
    assert capsys.readouterr().out == ""


def test_logger_va_a_logging_no_a_stdout(capsys, caplog):
    f = fabrica(INFO_ES)
    obtener_testigo("u", ydl_factory=f)
    lg = f.creadas[0].opts["logger"]
    with caplog.at_level(logging.DEBUG, logger="yti_cliente.ytdlp"):
        lg.debug("[debug] x")
        lg.info("i")
        lg.warning("w")
        lg.error("e")
    assert capsys.readouterr().out == ""
    assert {"i", "w", "e"} <= {r.getMessage() for r in caplog.records}


def test_auto_usa_writeautomaticsub():
    info = {"language": "es", "automatic_captions": {"es-orig": []}}
    f = fabrica(info)
    obtener_testigo("u", ydl_factory=f)
    assert f.creadas[1].opts["writeautomaticsub"] is True
