import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import respx

from yti_cliente.config import Config
from yti_cliente.ejecutor import (
    ErrorLocal,
    crear_contexto,
    encolar,
    enviar,
    estimacion,
    registrar,
)
from yti_cliente.testigo import InfoVideo, ResultadoTestigo, SinConexion

BASE = "https://api.test"
VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"
CLAVE_YTI = "clave-yti-secreta-0001"
CLAVE_GEMINI = "gemini-secreta-" + "g" * 20
CLAVE_ANTHROPIC = "sk-ant-secreta-" + "a" * 20
AHORA = datetime(2026, 11, 18, 14, 25, 30, tzinfo=timezone.utc)
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHola\n"


class TestigoFalso:
    __test__ = False

    def __init__(self, resultado=None, error=None):
        self.resultado = resultado or ResultadoTestigo(
            "obtenido", VTT, "es", None, InfoVideo("Un título", "Un canal", 912)
        )
        self.error = error
        self.llamadas: list[str] = []

    def __call__(self, url: str) -> ResultadoTestigo:
        self.llamadas.append(url)
        if self.error is not None:
            raise self.error
        return self.resultado


@dataclass
class Entorno:
    ctx: Any
    router: Any
    post: Any
    testigo: TestigoFalso
    cfg: Config

    @property
    def paginas(self) -> list[str]:
        return [c.request.url.path for c in self.router.calls]


def _cfg(tmp_path, **cambios) -> Config:
    datos = dict(
        api_url=BASE,
        biblioteca=tmp_path / "bib",
        clave_yti=CLAVE_YTI,
        clave_gemini=CLAVE_GEMINI,
        clave_anthropic=CLAVE_ANTHROPIC,
        origen={},
    )
    datos.update(cambios)
    return Config(**datos)


@pytest.fixture
def env(tmp_path):
    cfg = _cfg(tmp_path)
    testigo = TestigoFalso()
    ctx = crear_contexto(cfg, reloj=lambda: AHORA, obtener_testigo=testigo)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.get("/openapi.json").respond(json={"info": {"version": "0.2.2"}})
        router.get("/health/vivo").respond(200)
        post = router.post("/v1/jobs").respond(202, json={"id": "remoto-1"})
        yield Entorno(ctx, router, post, testigo, cfg)


def _json_cola(ctx):
    return sorted((ctx.cfg.biblioteca / ".yti" / "trabajos").glob("*.json"))


def _cuerpo(post, i=-1) -> dict:
    return json.loads(post.calls[i].request.content)


def _detalle(codigo, mensaje="m"):
    return {"detail": {"codigo": codigo, "mensaje": mensaje}}


def _front_a(objetivo):
    return {
        "video_id": VID, "objetivo": objetivo, "modelo": "m",
        "obtenido": "2026-11-18T10:00:00Z", "costo_usd": 0.01,
        "verificacion": "verificado", "coincidencia_pct": 87, "trabajo": "j",
    }


def _front_t():
    return {
        "video_id": VID, "titulo": "T", "canal": "C", "url": URL, "duracion": 1,
        "fuente": "captions", "fuente_testigo": "cliente", "verificacion": "verificado",
        "coincidencia_pct": 87, "obtenido": "2026-11-18T10:00:00Z", "trabajo": "j",
    }


# --- registrar: validaciones ---


def test_registrar_url_invalida(env):
    with pytest.raises(ErrorLocal) as e:
        registrar(env.ctx, "https://ejemplo.com/watch?v=" + VID)
    assert e.value.codigo == "URL_NO_ADMITIDA"
    assert len(env.router.calls) == 0
    assert _json_cola(env.ctx) == []


def test_objetivo_largo(env):
    with pytest.raises(ErrorLocal) as e:
        registrar(env.ctx, URL, objetivo="x" * 1001)
    assert e.value.codigo == "OBJETIVO_LARGO"
    assert _json_cola(env.ctx) == []


def test_objetivo_solo_espacios(env):
    r = registrar(env.ctx, URL, objetivo="   ")
    assert r.trabajo["objetivo"] is None


def test_falta_clave_anthropic(tmp_path):
    cfg = _cfg(tmp_path, clave_anthropic=None)
    ctx = crear_contexto(cfg, reloj=lambda: AHORA, obtener_testigo=TestigoFalso())
    with pytest.raises(ErrorLocal) as e:
        registrar(ctx, URL)
    assert e.value.codigo == "FALTA_CLAVE"
    assert "--solo-transcripcion" in e.value.mensaje
    assert "yti config --clave anthropic" in e.value.mensaje
    r = registrar(ctx, URL, solo_transcripcion=True)
    assert r.duplicado is False


@pytest.mark.parametrize("campo,nombre", [("clave_yti", "yt-insights"), ("clave_gemini", "gemini")])
def test_falta_clave_obligatoria(tmp_path, campo, nombre):
    ctx = crear_contexto(_cfg(tmp_path, **{campo: None}), reloj=lambda: AHORA)
    with pytest.raises(ErrorLocal) as e:
        registrar(ctx, URL, solo_transcripcion=True)
    assert e.value.codigo == "FALTA_CLAVE"
    assert f"yti config --clave {nombre}" in e.value.mensaje


def test_clave_mal_formada_sin_valor(tmp_path):
    mala = "corta con espacio!"
    ctx = crear_contexto(_cfg(tmp_path, clave_gemini=mala), reloj=lambda: AHORA)
    with pytest.raises(ErrorLocal) as e:
        registrar(ctx, URL)
    assert e.value.codigo == "CLAVE_MAL_FORMADA"
    assert "gemini" in e.value.mensaje
    assert mala not in e.value.mensaje


# --- duplicados ---


def test_duplicado_biblioteca(env):
    ruta = env.ctx.biblioteca.escribir_analisis(VID, "T", " Migrar ", _front_a("Migrar"), "cuerpo")
    r = registrar(env.ctx, URL, objetivo=" migrar  ")
    assert r.duplicado is True
    assert str(ruta) in r.mensaje
    assert "yti encolar" in r.mensaje and "--forzar" in r.mensaje
    assert r.trabajo is None
    assert len(env.router.calls) == 0


def test_duplicado_forzar(env):
    env.ctx.biblioteca.escribir_analisis(VID, "T", "Migrar", _front_a("Migrar"), "cuerpo")
    r = encolar(env.ctx, URL, objetivo="Migrar", forzar=True)
    assert r.duplicado is False
    assert r.trabajo["estado"] == "enviado"
    assert env.post.call_count == 1


def test_duplicado_transcripcion(env):
    env.ctx.biblioteca.escribir_transcripcion(VID, "T", _front_t(), "texto")
    r = registrar(env.ctx, URL, solo_transcripcion=True)
    assert r.duplicado is True
    assert "transcripcion.md" in r.mensaje


def test_duplicado_en_cola(env):
    primero = registrar(env.ctx, URL, objetivo="Migrar")
    segundo = registrar(env.ctx, URL, objetivo=" MIGRAR ")
    assert segundo.duplicado is True
    assert primero.trabajo["id_local"] in segundo.mensaje
    assert "--forzar" in segundo.mensaje


def test_error_no_bloquea(env):
    r1 = registrar(env.ctx, URL)
    env.ctx.cola.liberar(r1.trabajo, estado="error")
    r2 = registrar(env.ctx, URL)
    assert r2.duplicado is False


def test_duplicado_con_proyecto_en_biblioteca(env):
    env.ctx.biblioteca.escribir_transcripcion(VID, "T", _front_t(), "texto")
    env.ctx.biblioteca.escribir_analisis(VID, "T", None, _front_a(None), "cuerpo")
    r = registrar(env.ctx, URL, proyecto="x")
    assert r.duplicado is True
    texto = (env.cfg.biblioteca / "proyectos" / "x.md").read_text(encoding="utf-8")
    assert "../videos/" in texto


def test_duplicado_con_proyecto_en_cola(env):
    primero = registrar(env.ctx, URL)
    assert primero.trabajo["proyecto"] is None
    r = registrar(env.ctx, URL, proyecto="x")
    assert r.duplicado is True
    assert env.ctx.cola.leer(primero.trabajo["id_local"])["proyecto"] == "x"


# --- enviar ---


def test_registrar_deja_trabajo_tomado(env):
    r = registrar(env.ctx, URL, objetivo="  Migrar  ")
    t = r.trabajo
    assert t["estado"] == "enviando"
    assert t["tomado_por"] == env.ctx.cola.id_proceso
    assert t["testigo_estado"] == "pendiente"
    assert t["objetivo"] == "Migrar"
    assert t["video_id"] == VID and t["modo"] == "analisis"
    assert r.estimacion == "entre 2 y 10 minutos según la duración"
    assert len(env.router.calls) == 0


def test_enviar_202_con_testigo(env):
    r = encolar(env.ctx, URL, objetivo="Migrar")
    t = env.ctx.cola.leer(r.trabajo["id_local"])
    cuerpo = _cuerpo(env.post)
    assert cuerpo["testigo"] == {"formato": "vtt", "idioma": "es", "contenido": VTT}
    assert cuerpo["objetivo"] == "Migrar"
    assert t["estado"] == "enviado"
    assert t["id_remoto"] == "remoto-1"
    assert t["estado_remoto"] == "pendiente" and t["intentos_remotos"] == 0
    assert t["testigo_estado"] == "obtenido"
    assert t["tomado_por"] is None and t["tomado_en"] is None
    assert t["titulo_local"] == "Un título" and t["canal_local"] == "Un canal"
    assert t["duracion_s"] == 912
    assert (env.cfg.biblioteca / ".yti" / "testigos" / f"{t['id_local']}.vtt").exists()
    assert r.estimacion == "unos 5 minutos"


def test_sin_testigo_no_toca_youtube(env):
    r = encolar(env.ctx, URL, sin_testigo=True)
    assert env.testigo.llamadas == []
    assert "testigo" not in _cuerpo(env.post)
    assert r.trabajo["testigo_estado"] == "desactivado"
    assert r.trabajo["estado"] == "enviado"


def test_sin_subtitulos_sigue(env):
    env.testigo.resultado = ResultadoTestigo(
        "sin_subtitulos", None, None, "el video no tiene subtítulos", InfoVideo("T", "C", 60)
    )
    r = encolar(env.ctx, URL)
    assert "testigo" not in _cuerpo(env.post)
    assert r.trabajo["testigo_estado"] == "sin_subtitulos"
    assert r.trabajo["motivo_sin_testigo"] == "el video no tiene subtítulos"
    assert r.trabajo["estado"] == "enviado"


def test_sin_conexion_por_enviar(env):
    env.testigo.error = SinConexion("sin red")
    r = encolar(env.ctx, URL)
    assert r.trabajo["estado"] == "por_enviar"
    assert r.trabajo["testigo_estado"] == "pendiente"
    assert r.trabajo["tomado_por"] is None
    assert env.post.call_count == 0


def test_tope_retry_after(env):
    env.post.respond(429, headers={"Retry-After": "7200"}, json=_detalle("TOPE_DIARIO"))
    r = encolar(env.ctx, URL)
    assert r.trabajo["estado"] == "retenido"
    assert r.trabajo["enviar_despues"] == "2026-11-18T16:25:30Z"
    assert "tope diario alcanzado; se enviará a partir de" in r.mensaje
    assert r.trabajo["tomado_por"] is None


def test_tope_sin_retry_after(env):
    env.post.respond(429, json=_detalle("TOPE_DIARIO"))
    r = encolar(env.ctx, URL)
    assert r.trabajo["estado"] == "retenido"
    assert r.trabajo["enviar_despues"] == "2026-11-19T00:00:00Z"


def test_tope_retry_after_invalido(env):
    env.post.respond(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
                     json=_detalle("TOPE_DIARIO"))
    r = encolar(env.ctx, URL)
    assert r.trabajo["enviar_despues"] == "2026-11-19T00:00:00Z"


def test_testigo_invalido_reenvia_una_vez(env):
    env.post.side_effect = [
        httpx.Response(422, json=_detalle("TESTIGO_INVALIDO")),
        httpx.Response(202, json={"id": "remoto-2"}),
    ]
    r = encolar(env.ctx, URL)
    assert env.post.call_count == 2
    assert "testigo" in _cuerpo(env.post, 0)
    assert "testigo" not in _cuerpo(env.post, 1)
    assert r.trabajo["estado"] == "enviado"
    assert r.trabajo["id_remoto"] == "remoto-2"
    assert r.trabajo["testigo_estado"] == "sin_subtitulos"
    assert r.trabajo["motivo_sin_testigo"] == "rechazado por el servidor (TESTIGO_INVALIDO)"


def test_testigo_invalido_dos_veces_es_error(env):
    env.post.respond(422, json=_detalle("TESTIGO_INVALIDO"))
    r = encolar(env.ctx, URL)
    assert env.post.call_count == 2
    assert r.trabajo["estado"] == "error"
    assert r.trabajo["error"]["codigo"] == "TESTIGO_INVALIDO"


@pytest.mark.parametrize(
    "estado,cuerpo,codigo",
    [
        (401, {}, "CLAVE_YTI_INVALIDA"),
        (400, _detalle("CLAVE_MAL_FORMADA"), "CLAVE_MAL_FORMADA"),
        (400, _detalle("FUENTE_NO_ADMITIDA"), "FUENTE_NO_ADMITIDA"),
        (400, _detalle("CALLBACK_NO_PERMITIDO"), "CALLBACK_NO_PERMITIDO"),
        (422, {"detail": [{"msg": "campo inválido"}]}, "VALIDACION"),
    ],
)
def test_errores_finales(env, estado, cuerpo, codigo):
    env.post.respond(estado, json=cuerpo)
    r = encolar(env.ctx, URL)
    t = env.ctx.cola.leer(r.trabajo["id_local"])
    assert t["estado"] == "error"
    assert t["error"]["codigo"] == codigo
    assert t["error"]["mensaje"]
    assert t["tomado_por"] is None
    assert env.ctx.cola.leer_testigo(t["id_local"]) is None
    assert env.post.call_count == 1
    if codigo in ("FUENTE_NO_ADMITIDA", "CALLBACK_NO_PERMITIDO"):
        assert "reporta" in t["error"]["mensaje"]


def test_contrato_incompatible_es_error(env):
    env.post.respond(202, json={"sin": "id"})
    r = encolar(env.ctx, URL)
    assert r.trabajo["estado"] == "error"
    assert r.trabajo["error"]["codigo"] == "CONTRATO_INCOMPATIBLE"
    assert env.ctx.cola.leer_testigo(r.trabajo["id_local"]) is None


@pytest.mark.parametrize(
    "falla",
    [
        httpx.Response(503, json=_detalle("BD_NO_DISPONIBLE")),
        httpx.Response(500),
        httpx.ConnectTimeout("x"),
    ],
)
def test_servicio_no_disponible(env, falla):
    env.post.side_effect = [falla]
    r = encolar(env.ctx, URL)
    assert r.trabajo["estado"] == "por_enviar"
    assert r.trabajo["tomado_por"] is None
    assert env.post.call_count == 1
    assert env.ctx.cola.leer_testigo(r.trabajo["id_local"]) == VTT


def test_respuesta_perdida(env):
    env.post.side_effect = [httpx.ReadTimeout("x")]
    r = encolar(env.ctx, URL)
    assert r.trabajo["estado"] == "por_enviar"
    assert r.trabajo["motivo_sin_envio"] == "respuesta perdida"
    assert env.post.call_count == 1


def test_version_incompatible_no_envia(env):
    env.router.get("/openapi.json").respond(json={"info": {"version": "0.3.0"}})
    r = encolar(env.ctx, URL)
    assert env.post.call_count == 0
    assert "/health/vivo" not in env.paginas
    assert r.trabajo["estado"] == "por_enviar"
    assert "uv tool upgrade yt-insights-cliente" in r.mensaje


def test_despertar_antes_del_post(env):
    encolar(env.ctx, URL)
    orden = [p for p in env.paginas if p in ("/health/vivo", "/v1/jobs")]
    assert orden == ["/health/vivo", "/v1/jobs"]


def test_enviar_reintenta_trabajo_por_enviar_con_testigo_guardado(env):
    env.post.side_effect = [httpx.Response(503), httpx.Response(202, json={"id": "r9"})]
    r = encolar(env.ctx, URL)
    assert r.trabajo["estado"] == "por_enviar"
    tomado = env.ctx.cola.tomar_para_envio()
    assert len(tomado) == 1
    t = enviar(env.ctx, tomado[0])
    assert t["estado"] == "enviado"
    assert _cuerpo(env.post)["testigo"]["contenido"] == VTT
    assert len(env.testigo.llamadas) == 1


def test_estimacion():
    assert estimacion(None) == "entre 2 y 10 minutos según la duración"
    assert estimacion(912) == "unos 5 minutos"
    assert estimacion(0) == "unos 2 minutos"


def test_sin_claves_en_trabajo_ni_mensajes(env):
    claves = (CLAVE_YTI, CLAVE_GEMINI, CLAVE_ANTHROPIC)
    mensajes = []
    mensajes.append(encolar(env.ctx, URL, objetivo="uno").mensaje)
    env.post.respond(400, json=_detalle("CLAVE_MAL_FORMADA", f"clave {CLAVE_GEMINI} rara"))
    mensajes.append(encolar(env.ctx, URL, objetivo="dos").mensaje)
    env.post.respond(429, json=_detalle("TOPE_DIARIO", f"tope {CLAVE_YTI}"))
    mensajes.append(encolar(env.ctx, URL, objetivo="tres").mensaje)
    env.post.side_effect = [httpx.ReadTimeout("x")]
    mensajes.append(encolar(env.ctx, URL, objetivo="cuatro").mensaje)
    archivos = list((env.cfg.biblioteca / ".yti").rglob("*.json"))
    assert len(archivos) == 4
    for archivo in archivos:
        texto = archivo.read_text(encoding="utf-8")
        assert not any(c in texto for c in claves)
    for m in mensajes:
        assert not any(c in m for c in claves)
