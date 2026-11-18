import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
import respx

from yti_cliente import cli
from yti_cliente.cola import iso
from yti_cliente.config import Config, cargar_config, ruta_config
from yti_cliente.ejecutor import crear_contexto
from yti_cliente.testigo import InfoVideo, ResultadoTestigo

BASE = "https://api.test"
VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"
CLAVE_YTI = "clave-yti-secreta-0001"
CLAVE_GEMINI = "gemini-secreta-" + "g" * 20
CLAVE_ANTHROPIC = "sk-ant-secreta-" + "a" * 20
AHORA = datetime(2026, 11, 18, 14, 25, 30, tzinfo=timezone.utc)
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHola\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = Config(
        api_url=BASE, biblioteca=tmp_path / "bib", clave_yti=CLAVE_YTI,
        clave_gemini=CLAVE_GEMINI, clave_anthropic=CLAVE_ANTHROPIC, origen={},
    )
    ctx = crear_contexto(
        cfg, reloj=lambda: AHORA,
        obtener_testigo=lambda url: ResultadoTestigo(
            "obtenido", VTT, "es", None, InfoVideo("Un título", "Un canal", 912)
        ),
    )
    monkeypatch.setenv("YTI_API_KEY", CLAVE_YTI)
    monkeypatch.setenv("YTI_GEMINI_KEY", CLAVE_GEMINI)
    monkeypatch.setenv("YTI_ANTHROPIC_KEY", CLAVE_ANTHROPIC)
    monkeypatch.setenv("YTI_BIBLIOTECA", str(cfg.biblioteca))
    monkeypatch.setattr(cli, "crear_contexto", lambda c: ctx)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.get("/openapi.json").respond(json={"info": {"version": "0.2.2"}})
        router.get("/health/vivo").respond(200)
        post = router.post("/v1/jobs").respond(202, json={"id": "r1"})
        router.get("/v1/jobs/r1").respond(json={"estado": "pendiente", "intentos": 2})
        yield SimpleNamespace(ctx=ctx, router=router, post=post, cfg=cfg)


def test_config_muestra_enmascarado(monkeypatch, capsys):
    clave = "gemini-" + "x" * 20 + "ABCD"
    monkeypatch.setenv("YTI_GEMINI_KEY", clave)
    assert cli.main(["config"]) == 0
    salida = capsys.readouterr().out
    assert "…ABCD" in salida
    assert "(entorno)" in salida
    assert clave not in salida


def test_config_clave_getpass(monkeypatch, capsys):
    clave = "g" * 24 + "WXYZ"
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: clave)
    assert cli.main(["config", "--clave", "gemini"]) == 0
    assert cargar_config(ruta_config(), {}).clave_gemini == clave
    capturado = capsys.readouterr()
    assert clave not in capturado.out + capturado.err


@pytest.mark.parametrize("valor", ["corta", "con espacios y símbolos !!!!!!!!!!!!", ""])
def test_config_clave_invalida(monkeypatch, capsys, valor):
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: valor)
    assert cli.main(["config", "--clave", "gemini"]) == 1
    assert not ruta_config().exists()
    assert "Traceback" not in capsys.readouterr().err


def test_config_biblioteca_y_api_url(tmp_path, capsys):
    destino = tmp_path / "mi-biblioteca"
    assert cli.main(["config", "--biblioteca", str(destino), "--api-url", "https://otra.test/"]) == 0
    cfg = cargar_config(ruta_config(), {})
    assert cfg.biblioteca == destino
    assert cfg.api_url == "https://otra.test"


def test_encolar_imprime_ids_y_estimacion(env, capsys):
    assert cli.main(["encolar", URL, "--solo-transcripcion"]) == 0
    salida = capsys.readouterr().out
    assert "r1" in salida
    assert "minutos" in salida
    (trabajo,) = env.ctx.cola.listar()
    assert trabajo["id_local"] in salida


def test_encolar_duplicado(env, capsys):
    assert cli.main(["encolar", URL, "--solo-transcripcion"]) == 0
    capsys.readouterr()
    assert cli.main(["encolar", URL, "--solo-transcripcion"]) == 0
    assert "--forzar" in capsys.readouterr().out
    assert len(env.post.calls) == 1


def test_encolar_url_invalida(env, capsys):
    assert cli.main(["encolar", "https://ejemplo.com/nada"]) == 1
    err = capsys.readouterr().err.strip()
    assert len(err.splitlines()) == 1
    assert "Traceback" not in err


def test_estado_tabla(env, capsys):
    base = {"url": URL, "video_id": VID, "modo": "solo_transcripcion", "objetivo": None,
            "objetivo_normalizado": "", "testigo_estado": "sin_subtitulos"}
    a = env.ctx.cola.crear(dict(base))
    a = env.ctx.cola.liberar(a, estado="retenido", enviar_despues=iso(datetime(2026, 11, 19, tzinfo=timezone.utc)))
    b = env.ctx.cola.crear(dict(base, duracion_s=600))
    b = env.ctx.cola.liberar(b, estado="enviado", id_remoto="r1", estado_remoto="pendiente")
    assert cli.main(["estado"]) == 0
    salida = capsys.readouterr().out
    assert a["id_local"] in salida and b["id_local"] in salida
    assert "retenido" in salida and "enviado" in salida
    assert "minutos" in salida


def test_estado_cola_vacia(env, capsys):
    assert cli.main(["estado"]) == 0
    assert "vacía" in capsys.readouterr().out


def test_esperar_ctrl_c(env, monkeypatch, capsys):
    cli_trabajo = env.ctx.cola.crear({"url": URL, "video_id": VID, "modo": "solo_transcripcion",
                                       "objetivo": None, "objetivo_normalizado": ""})
    antes = env.ctx.cola.leer(cli_trabajo["id_local"])

    def falso(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "esperar", falso)
    assert cli.main(["esperar"]) == 130
    assert env.ctx.cola.leer(cli_trabajo["id_local"]) == antes


def test_esperar_imprime_tabla(env, monkeypatch, capsys):
    t = env.ctx.cola.crear({"url": URL, "video_id": VID, "modo": "solo_transcripcion",
                            "objetivo": None, "objetivo_normalizado": ""})
    monkeypatch.setattr(cli, "esperar", lambda ctx, id_local=None, tope_min=30: [env.ctx.cola.leer(t["id_local"])])
    assert cli.main(["esperar", t["id_local"], "--tope-min", "5"]) == 0
    assert t["id_local"] in capsys.readouterr().out


def test_lista_buscar(env, capsys):
    env.ctx.biblioteca.escribir_transcripcion(
        VID, "Cómo migrar", {"video_id": VID, "titulo": "Cómo migrar", "canal": "Canal X"}, "cuerpo"
    )
    assert cli.main(["lista", "--buscar", "migrar"]) == 0
    assert VID in capsys.readouterr().out
    assert cli.main(["lista", "--buscar", "inexistente"]) == 0
    assert VID not in capsys.readouterr().out


def test_mcp_delega(monkeypatch):
    llamadas = []
    monkeypatch.setitem(sys.modules, "yti_cliente.mcp_server", SimpleNamespace(ejecutar=lambda: llamadas.append(1)))
    assert cli.main(["mcp"]) == 0
    assert llamadas == [1]


def test_sin_claves_en_salida_ni_logs(env, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: CLAVE_GEMINI)
    assert cli.main(["config", "--clave", "gemini"]) == 0
    assert cli.main(["config"]) == 0
    assert cli.main(["encolar", URL, "--solo-transcripcion"]) == 0
    assert cli.main(["estado"]) == 0
    capturado = capsys.readouterr()
    log = (tmp_path / "log" / "yti.log").read_text(encoding="utf-8")
    for clave in (CLAVE_YTI, CLAVE_GEMINI, CLAVE_ANTHROPIC):
        assert clave not in capturado.out + capturado.err + log


def test_error_inesperado_una_linea(env, monkeypatch, capsys):
    def falla(ctx):
        raise OSError("disco lleno")

    monkeypatch.setattr(cli, "activar", falla)
    assert cli.main(["estado"]) == 1
    err = capsys.readouterr().err.strip()
    assert len(err.splitlines()) == 1 and "Traceback" not in err


def test_error_inesperado_redacta_claves(env, monkeypatch, capsys):
    def falla(ctx):
        raise OSError(f"fallo con {CLAVE_GEMINI} y {CLAVE_YTI}")

    monkeypatch.setattr(cli, "activar", falla)
    assert cli.main(["estado"]) == 1
    err = capsys.readouterr().err
    assert CLAVE_GEMINI not in err and CLAVE_YTI not in err and "***" in err


def test_config_biblioteca_relativa_se_guarda_absoluta(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["config", "--biblioteca", "rel/bib"]) == 0
    assert cargar_config(ruta_config(), {}).biblioteca == (tmp_path / "rel" / "bib").resolve()


def test_config_idioma_se_guarda(capsys):
    assert cli.main(["config", "--idioma", "pt-BR"]) == 0
    assert cargar_config(ruta_config(), {}).idioma == "pt-BR"
    assert "idioma: pt-BR" in capsys.readouterr().out


def test_config_idioma_invalido(capsys):
    assert cli.main(["config", "--idioma", "ES_es"]) == 1
    assert not ruta_config().exists()
    assert "Error:" in capsys.readouterr().err


def test_config_muestra_idioma_con_origen(monkeypatch, capsys):
    monkeypatch.setenv("YTI_IDIOMA", "en")
    assert cli.main(["config"]) == 0
    assert "idioma: en (entorno)" in capsys.readouterr().out


def test_encolar_no_tiene_flag_idioma():
    with pytest.raises(SystemExit):
        cli.main(["encolar", URL, "--idioma", "en"])
