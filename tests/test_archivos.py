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
