import os
import socket

import platformdirs
import pytest

_HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1", None}
_getaddrinfo_real = socket.getaddrinfo


@pytest.fixture(autouse=True)
def aislar_entorno(monkeypatch, tmp_path):
    for nombre in [n for n in os.environ if n.startswith("YTI_")]:
        monkeypatch.delenv(nombre, raising=False)
    monkeypatch.setattr(
        platformdirs, "user_config_path", lambda *a, **k: tmp_path / "config"
    )
    monkeypatch.setattr(platformdirs, "user_log_path", lambda *a, **k: tmp_path / "log")
    monkeypatch.setattr(
        platformdirs, "user_state_path", lambda *a, **k: tmp_path / "state"
    )


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    def _getaddrinfo(host, *args, **kwargs):
        if host in _HOSTS_LOCALES:
            return _getaddrinfo_real(host, *args, **kwargs)
        raise RuntimeError("red prohibida en pruebas")

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def proyectos(tmp_path):
    from datetime import datetime, timezone

    from yti_cliente.biblioteca import Biblioteca
    from yti_cliente.cola import Cola
    from yti_cliente.proyectos import Proyectos

    ahora = datetime(2026, 11, 26, 15, 0, 0, tzinfo=timezone.utc)
    return Proyectos(Biblioteca(tmp_path / "bib"), Cola(tmp_path / "bib", reloj=lambda: ahora))
