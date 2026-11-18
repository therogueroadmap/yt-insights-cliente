import pytest

from yti_cliente.archivos import escribir_atomico


def test_escribir_atomico_crea_padres_y_contenido(tmp_path):
    ruta = tmp_path / "a" / "b" / "x.md"
    escribir_atomico(ruta, "hola ñ\n")
    assert ruta.read_text(encoding="utf-8") == "hola ñ\n"
    assert [p.name for p in ruta.parent.iterdir()] == ["x.md"]  # sin temporales


def test_escribir_atomico_reemplaza(tmp_path):
    ruta = tmp_path / "x.md"
    escribir_atomico(ruta, "1")
    escribir_atomico(ruta, "2")
    assert ruta.read_text(encoding="utf-8") == "2"


def test_conftest_aisla_platformdirs(tmp_path):
    import platformdirs

    assert str(platformdirs.user_config_path("yt-insights-cliente")).startswith(str(tmp_path))


def test_conftest_sin_red():
    import socket

    with pytest.raises(RuntimeError):
        socket.getaddrinfo("example.com", 443)


def test_escribir_atomico_reintenta_ante_permission_error(tmp_path, monkeypatch):
    import os

    from yti_cliente import archivos

    real = os.replace
    llamadas = {"n": 0}

    def flaky(a, b):
        llamadas["n"] += 1
        if llamadas["n"] < 3:
            raise PermissionError("abierto por otro lector")
        return real(a, b)

    monkeypatch.setattr(archivos.os, "replace", flaky)
    monkeypatch.setattr(archivos.time, "sleep", lambda s: None)
    ruta = tmp_path / "x.md"
    escribir_atomico(ruta, "ok")
    assert ruta.read_text(encoding="utf-8") == "ok" and llamadas["n"] == 3
    assert [p.name for p in tmp_path.iterdir()] == ["x.md"]


def test_escribir_atomico_relanza_tras_agotar_reintentos(tmp_path, monkeypatch):
    from yti_cliente import archivos

    monkeypatch.setattr(archivos.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("x")))
    monkeypatch.setattr(archivos.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        escribir_atomico(tmp_path / "x.md", "ok")
    assert list(tmp_path.iterdir()) == []
