import asyncio
import dataclasses
import threading
from datetime import datetime, timezone

import pytest
import respx
from mcp import Client

import yti_cliente.mcp_server as mcp_server
from yti_cliente.config import Config
from yti_cliente.ejecutor import ResumenActivacion, crear_contexto
from yti_cliente.mcp_server import (
    INTERVALO_SONDEO_S,
    Servicio,
    ciclo_sondeo,
    construir_servidor,
)
from yti_cliente.testigo import InfoVideo, ResultadoTestigo

BASE = "https://api.test"
VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"
CLAVE_YTI = "clave-yti-secreta-0001"
CLAVE_GEMINI = "gemini-secreta-" + "g" * 20
CLAVE_ANTHROPIC = "sk-ant-secreta-" + "a" * 20
CLAVES = (CLAVE_YTI, CLAVE_GEMINI, CLAVE_ANTHROPIC)
AHORA = datetime(2026, 11, 18, 14, 25, 30, tzinfo=timezone.utc)
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHola\n"
OBJETIVO = "Migrar a microservicios"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _testigo(url):
    return ResultadoTestigo("obtenido", VTT, "es", None, InfoVideo("Un título", "Un canal", 912))


class Entorno:
    def __init__(self, ctx, router, post, servicio):
        self.ctx, self.router, self.post, self.servicio = ctx, router, post, servicio


@pytest.fixture
def env(tmp_path):
    cfg = Config(
        api_url=BASE, biblioteca=tmp_path / "bib", clave_yti=CLAVE_YTI,
        clave_gemini=CLAVE_GEMINI, clave_anthropic=CLAVE_ANTHROPIC, origen={},
    )
    ctx = crear_contexto(cfg, reloj=lambda: AHORA, obtener_testigo=_testigo)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.get("/openapi.json").respond(json={"info": {"version": "0.2.2"}})
        router.get("/health/vivo").respond(200)
        post = router.post("/v1/jobs").respond(202, json={"id": "remoto-1"})
        yield Entorno(ctx, router, post, Servicio(ctx))


def _vista_completada():
    return {
        "id": "remoto-1", "estado": "completado", "video_id": VID, "modo": "analisis",
        "modelo": "modelo-x", "intentos": 1, "metodo_transcripcion": "captions",
        "fuente_testigo": "cliente", "objetivo": OBJETIVO,
        "resultado": {
            "metadatos": {"titulo": "Cómo migrar", "canal": "Canal X", "duracion_s": 600},
            "transcripcion_md": "# Transcripción\n\nHola mundo.\n",
            "analisis": {"resumen": "Un resumen.", "puntos_clave": [{"segundo": 65, "texto": "Uno"}]},
            "verificacion": {"estado": "verificado", "coincidencia_pct": 87},
        },
        "uso": {"costo_usd": {"total": 0.01}},
    }


def _sembrar_enviado(ctx, **extra):
    datos = {
        "url": URL, "video_id": VID, "modo": "analisis", "objetivo": OBJETIVO,
        "objetivo_normalizado": OBJETIVO.lower(), "testigo_estado": "sin_subtitulos",
    }
    datos.update(extra)
    trabajo = ctx.cola.crear(datos)
    return ctx.cola.liberar(trabajo, estado="enviado", id_remoto="remoto-1", estado_remoto="pendiente")


async def test_cuatro_herramientas(env):
    herramientas = await construir_servidor(env.servicio).list_tools()
    assert {t.name for t in herramientas} == {
        "encolar_video", "ver_cola", "listar_biblioteca", "leer_resultado"
    }
    por_nombre = {t.name: t for t in herramientas}
    assert set(por_nombre["leer_resultado"].input_schema["properties"]) == {
        "video", "objetivo", "contenido"
    }
    assert set(por_nombre["encolar_video"].input_schema["properties"]) == {
        "url", "objetivo", "solo_transcripcion"
    }
    for t in herramientas:
        assert "self" not in t.input_schema.get("properties", {})
        assert "self" not in t.input_schema.get("required", [])
        assert t.description


async def test_cliente_en_memoria_y_lifespan(env, monkeypatch):
    eventos = []

    async def falso(servicio, **kw):
        eventos.append("arrancado")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            eventos.append("cancelado")
            raise

    monkeypatch.setattr(mcp_server, "ciclo_sondeo", falso)
    async with Client(construir_servidor(env.servicio)) as cliente:
        res = await cliente.call_tool("listar_biblioteca", {})
        assert res.is_error is False
        assert isinstance(res.content[0].text, str)
        assert "arrancado" in eventos
        assert "cancelado" not in eventos
    assert "cancelado" in eventos


async def test_encolar_retorna_antes_del_post(env):
    liberar = threading.Event()
    en_testigo = threading.Event()

    def bloqueante(url):
        en_testigo.set()
        assert liberar.wait(10)
        return _testigo(url)

    env.ctx.obtener_testigo = bloqueante
    texto = await env.servicio.encolar_video(URL, OBJETIVO)
    trabajos = env.ctx.cola.listar()
    assert len(trabajos) == 1
    id_local = trabajos[0]["id_local"]
    assert id_local in texto
    assert "entre 2 y 10 minutos" in texto
    assert len(env.post.calls) == 0
    assert await asyncio.to_thread(en_testigo.wait, 10)
    assert env.ctx.cola.leer(id_local)["estado"] == "enviando"
    assert len(env.post.calls) == 0
    liberar.set()
    await env.servicio.esperar_fondo()
    assert env.ctx.cola.leer(id_local)["estado"] == "enviado"
    assert len(env.post.calls) == 1


async def test_encolar_duplicado_mcp(env):
    _sembrar_enviado(env.ctx)
    texto = await env.servicio.encolar_video(URL, OBJETIVO)
    assert "yti encolar" in texto and "--forzar" in texto
    await env.servicio.esperar_fondo()
    assert len(env.post.calls) == 0


async def test_encolar_url_invalida_texto(env):
    texto = await env.servicio.encolar_video("https://ejemplo.com/nada")
    assert isinstance(texto, str) and texto
    assert env.ctx.cola.listar() == []


async def test_encolar_sin_claves_devuelve_texto(env):
    env.ctx.cfg = dataclasses.replace(env.ctx.cfg, clave_gemini=None)
    texto = await env.servicio.encolar_video(URL, OBJETIVO)
    assert "Gemini" in texto
    assert env.ctx.cola.listar() == []


async def test_error_en_fondo_se_registra_y_no_lanza(env, caplog):
    def roto(url):
        raise RuntimeError("fallo inesperado del testigo")

    env.ctx.obtener_testigo = roto
    await env.servicio.encolar_video(URL, OBJETIVO)
    await env.servicio.esperar_fondo()  # no debe lanzar
    assert "fallo inesperado del testigo" in caplog.text


async def test_ver_cola_baja_resultado(env):
    _sembrar_enviado(env.ctx)
    env.router.get("/v1/jobs/remoto-1").respond(json=_vista_completada())
    texto = await env.servicio.ver_cola()
    assert "Recién bajados" in texto
    assert VID in texto
    assert (env.ctx.cfg.biblioteca / "indice.md").is_file()


async def test_ver_cola_vacia_y_activo(env):
    assert "vacía" in (await env.servicio.ver_cola()).lower()
    _sembrar_enviado(env.ctx, duracion_s=600)
    env.router.get("/v1/jobs/remoto-1").respond(json={"estado": "procesando", "intentos": 1})
    texto = await env.servicio.ver_cola()
    assert "enviado" in texto
    assert "unos 4 minutos" in texto


async def test_ver_cola_error_inesperado_es_texto(env, monkeypatch):
    def roto(ctx):
        raise OSError("disco lleno")

    monkeypatch.setattr(mcp_server, "activar", roto)
    texto = await env.servicio.ver_cola()
    assert isinstance(texto, str) and texto


async def test_leer_resultado_con_url(env):
    env.ctx.biblioteca.escribir_transcripcion(
        VID, "Cómo migrar", {"video_id": VID, "titulo": "Cómo migrar", "canal": "C"},
        "Cuerpo de la transcripción.\n",
    )
    texto = await env.servicio.leer_resultado(f"https://youtu.be/{VID}", contenido="transcripcion")
    assert "Cuerpo de la transcripción." in texto


async def test_listar_biblioteca_buscar(env):
    env.ctx.biblioteca.escribir_transcripcion(
        VID, "Cómo migrar", {"video_id": VID, "titulo": "Cómo migrar", "canal": "Canal X"}, "x\n"
    )
    assert VID in await env.servicio.listar_biblioteca()
    assert VID in await env.servicio.listar_biblioteca("migrar")
    assert VID not in await env.servicio.listar_biblioteca("zzzz")


async def test_ciclo_sondeo_solo_con_activos(env, monkeypatch):
    llamadas = []

    def falso_activar(ctx):
        llamadas.append(1)
        return ResumenActivacion([], [], [], [])

    monkeypatch.setattr(mcp_server, "activar", falso_activar)
    tarea = asyncio.create_task(ciclo_sondeo(env.servicio, intervalo_s=0.01))
    await asyncio.sleep(0.1)
    assert llamadas == []
    _sembrar_enviado(env.ctx)
    await asyncio.sleep(0.1)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea
    assert len(llamadas) >= 1


async def test_ciclo_sondeo_sigue_tras_error(env, monkeypatch):
    llamadas = []

    def roto(ctx):
        llamadas.append(1)
        raise OSError("fallo")

    monkeypatch.setattr(mcp_server, "activar", roto)
    _sembrar_enviado(env.ctx)
    tarea = asyncio.create_task(ciclo_sondeo(env.servicio, intervalo_s=0.01))
    await asyncio.sleep(0.15)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea
    assert len(llamadas) >= 2


def test_intervalo_por_defecto():
    assert INTERVALO_SONDEO_S == 180


async def test_salidas_sin_claves_ni_stdout(env, capsys):
    salidas = [await env.servicio.encolar_video(URL, OBJETIVO)]
    await env.servicio.esperar_fondo()
    env.router.get("/v1/jobs/remoto-1").respond(json=_vista_completada())
    salidas.append(await env.servicio.ver_cola())
    salidas.append(await env.servicio.listar_biblioteca())
    salidas.append(await env.servicio.leer_resultado(VID))
    salidas.append(await env.servicio.encolar_video("no es url"))
    for texto in salidas:
        assert isinstance(texto, str)
        for clave in CLAVES:
            assert clave not in texto
    assert capsys.readouterr().out == ""


async def test_ver_cola_anuncia_lo_que_bajo_el_sondeo_de_fondo(env):
    _sembrar_enviado(env.ctx)
    env.router.get("/v1/jobs/remoto-1").respond(json=_vista_completada())
    tarea = asyncio.create_task(ciclo_sondeo(env.servicio, intervalo_s=0.01))
    await asyncio.sleep(0.3)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea
    texto = await env.servicio.ver_cola()
    assert "Recién bajados" in texto and VID in texto
    assert "vacía" in (await env.servicio.ver_cola()).lower()  # se vacía tras informar


async def test_ver_cola_etiqueta_general_sin_objetivo(env):
    env.ctx.cola.liberar(
        env.ctx.cola.crear({"url": URL, "video_id": VID, "modo": "analisis", "objetivo": None,
                            "objetivo_normalizado": "general", "testigo_estado": "sin_subtitulos"}),
        estado="retenido", enviar_despues="2099-01-01T00:00:00Z",
    )
    texto = await env.servicio.ver_cola()
    assert "general" in texto and "solo transcripción" not in texto


async def test_ver_cola_conserva_avisos_si_falla_la_lectura(env, monkeypatch):
    env.servicio.registrar_bajados([{"video_id": VID, "objetivo": OBJETIVO, "modo": "analisis"}])
    original = env.ctx.cola.listar

    def roto():
        raise OSError("cola ilegible")

    monkeypatch.setattr(env.ctx.cola, "listar", roto)
    texto = await env.servicio.ver_cola()
    assert "No se pudo leer la cola" in texto
    monkeypatch.setattr(env.ctx.cola, "listar", original)
    texto = await env.servicio.ver_cola()
    assert "Recién bajados" in texto and VID in texto


async def test_apagado_espera_envio_en_curso(env, monkeypatch):
    import time

    terminados = []

    def lento(ctx, trabajo):
        time.sleep(0.2)
        terminados.append(trabajo["id_local"])

    async def ciclo_quieto(servicio, **kw):
        await asyncio.sleep(3600)

    monkeypatch.setattr(mcp_server, "enviar", lento)
    monkeypatch.setattr(mcp_server, "ciclo_sondeo", ciclo_quieto)
    async with Client(construir_servidor(env.servicio)) as cliente:
        await cliente.call_tool("encolar_video", {"url": URL, "objetivo": OBJETIVO})
        assert env.servicio._tareas
    assert len(terminados) == 1


async def test_listar_biblioteca_error_inesperado_es_texto_redactado(env, monkeypatch):
    def roto(buscar=None):
        raise ValueError(f"fallo con la clave {CLAVE_YTI}")

    monkeypatch.setattr(env.ctx.biblioteca, "listar", roto)
    texto = await env.servicio.listar_biblioteca()
    assert "No se pudo leer la biblioteca" in texto
    assert "ValueError" in texto
    assert CLAVE_YTI not in texto
    assert "***" in texto
