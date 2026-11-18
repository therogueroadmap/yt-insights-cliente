import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from yti_cliente.api import (
    ClaveYtiInvalida,
    ClienteApi,
    ContratoIncompatible,
    ErrorApi,
    RechazoCreacion,
    RespuestaPerdida,
    ServicioNoDisponible,
    TestigoInvalido,
    TopeDiario,
    TrabajoNoEncontrado,
    contrato_compatible,
    parsear_retry_after,
    version_compatible,
)

BASE = "https://api.test"
CLAVE_YTI = "yti-clave-secreta-0123456789"
CLAVE_GEMINI = "gemini-clave-secreta-0123456789"
CLAVE_ANTHROPIC = "anthropic-clave-secreta-0123456789"
CLAVES = (CLAVE_YTI, CLAVE_GEMINI, CLAVE_ANTHROPIC)


@pytest.fixture
def mock():
    with respx.mock(base_url=BASE, assert_all_called=False) as m:
        yield m


@pytest.fixture
def cliente():
    with ClienteApi(BASE, CLAVE_YTI) as c:
        yield c


def crear(cliente, **kw):
    args = dict(
        url="https://youtu.be/XXXXXXXXXXX",
        modo="analisis",
        objetivo="migrar",
        testigo={"formato": "vtt", "idioma": "es", "contenido": "WEBVTT"},
        clave_gemini=CLAVE_GEMINI,
        clave_anthropic=CLAVE_ANTHROPIC,
    )
    args.update(kw)
    return cliente.crear_trabajo(**args)


def test_crear_202_cabeceras_y_cuerpo(mock, cliente):
    ruta = mock.post("/v1/jobs").respond(202, json={"id": "abc"})
    assert crear(cliente) == "abc"
    req = ruta.calls.last.request
    assert req.headers["X-API-Key"] == CLAVE_YTI
    assert req.headers["X-Gemini-Key"] == CLAVE_GEMINI
    assert req.headers["X-Anthropic-Key"] == CLAVE_ANTHROPIC
    assert json.loads(req.content) == {
        "url": "https://youtu.be/XXXXXXXXXXX",
        "modo": "analisis",
        "objetivo": "migrar",
        "testigo": {"formato": "vtt", "idioma": "es", "contenido": "WEBVTT"},
    }


@pytest.mark.parametrize("objetivo", [None, "  "])
def test_crear_transcripcion_sin_anthropic(mock, cliente, objetivo):
    ruta = mock.post("/v1/jobs").respond(202, json={"id": "abc"})
    crear(
        cliente,
        modo="transcripcion",
        objetivo=objetivo,
        testigo=None,
        clave_anthropic=None,
    )
    req = ruta.calls.last.request
    assert "X-Anthropic-Key" not in req.headers
    assert json.loads(req.content) == {
        "url": "https://youtu.be/XXXXXXXXXXX",
        "modo": "transcripcion",
    }


def test_crear_401_cadena(mock, cliente):
    mock.post("/v1/jobs").respond(401, json={"detail": "no autorizado"})
    with pytest.raises(ClaveYtiInvalida) as e:
        crear(cliente)
    assert e.value.codigo == "CLAVE_YTI_INVALIDA"
    assert e.value.http == 401


@pytest.mark.parametrize(
    "codigo",
    [
        "FALTA_CLAVE_GEMINI",
        "FALTA_CLAVE_ANTHROPIC",
        "CLAVE_MAL_FORMADA",
        "CALLBACK_NO_PERMITIDO",
        "FUENTE_NO_ADMITIDA",
    ],
)
def test_crear_400_objeto(mock, cliente, codigo):
    mock.post("/v1/jobs").respond(
        400, json={"detail": {"codigo": codigo, "mensaje": "detalle"}}
    )
    with pytest.raises(RechazoCreacion) as e:
        crear(cliente)
    assert e.value.codigo == codigo
    assert e.value.mensaje == "detalle"
    assert e.value.http == 400


def test_crear_422_testigo(mock, cliente):
    mock.post("/v1/jobs").respond(
        422, json={"detail": {"codigo": "TESTIGO_INVALIDO", "mensaje": "vacío"}}
    )
    with pytest.raises(TestigoInvalido) as e:
        crear(cliente)
    assert e.value.codigo == "TESTIGO_INVALIDO"


def test_crear_422_lista(mock, cliente):
    mock.post("/v1/jobs").respond(
        422, json={"detail": [{"msg": "primero", "loc": []}, {"msg": "segundo"}]}
    )
    with pytest.raises(RechazoCreacion) as e:
        crear(cliente)
    assert e.value.codigo == "VALIDACION"
    assert e.value.mensaje == "primero"


def test_crear_429_retry_after(mock, cliente):
    mock.post("/v1/jobs").respond(
        429,
        headers={"Retry-After": "3600"},
        json={"detail": {"codigo": "TOPE_DIARIO", "mensaje": "tope"}},
    )
    with pytest.raises(TopeDiario) as e:
        crear(cliente)
    assert e.value.retry_after_s == 3600
    assert e.value.codigo == "TOPE_DIARIO"


@pytest.mark.parametrize(
    "valor", ["abc", "-5", "Wed, 21 Oct 2026 07:28:00 GMT", None, "", "1.5"]
)
def test_retry_after_invalido(valor):
    assert parsear_retry_after(valor) is None


def test_retry_after_valido():
    assert parsear_retry_after("0") == 0
    assert parsear_retry_after(" 120 ") == 120


def test_crear_429_sin_retry_after(mock, cliente):
    mock.post("/v1/jobs").respond(429, json={"detail": "tope"})
    with pytest.raises(TopeDiario) as e:
        crear(cliente)
    assert e.value.retry_after_s is None


def test_crear_503_bd(mock, cliente):
    mock.post("/v1/jobs").respond(
        503, json={"detail": {"codigo": "BD_NO_DISPONIBLE", "mensaje": "bd"}}
    )
    with pytest.raises(ServicioNoDisponible) as e:
        crear(cliente)
    assert e.value.codigo == "BD_NO_DISPONIBLE"


def test_crear_502_html(mock, cliente):
    mock.post("/v1/jobs").respond(502, text="<html>Bad Gateway</html>")
    with pytest.raises(ServicioNoDisponible) as e:
        crear(cliente)
    assert e.value.codigo == "SERVICIO_NO_DISPONIBLE"


def test_crear_connect_timeout(mock, cliente):
    mock.post("/v1/jobs").mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(ServicioNoDisponible):
        crear(cliente)


def test_crear_error_red(mock, cliente):
    mock.post("/v1/jobs").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(ServicioNoDisponible):
        crear(cliente)


@pytest.mark.parametrize("exc", [httpx.ReadTimeout, httpx.WriteTimeout])
def test_crear_read_timeout(mock, cliente, exc):
    ruta = mock.post("/v1/jobs").mock(side_effect=exc("t"))
    with pytest.raises(RespuestaPerdida) as e:
        crear(cliente)
    assert e.value.codigo == "RESPUESTA_PERDIDA"
    assert ruta.call_count == 1


def test_crear_202_sin_id(mock, cliente):
    mock.post("/v1/jobs").respond(202, json={"otro": 1})
    with pytest.raises(ContratoIncompatible) as e:
        crear(cliente)
    assert e.value.codigo == "CONTRATO_INCOMPATIBLE"


def test_consultar_campos_desconocidos(mock, cliente):
    vista = {"estado": "completado", "nuevo": 1, "resultado": {"a": 1}}
    ruta = mock.get("/v1/jobs/abc").respond(200, json=vista)
    assert cliente.consultar("abc") == vista
    req = ruta.calls.last.request
    assert req.headers["X-API-Key"] == CLAVE_YTI
    assert "X-Gemini-Key" not in req.headers
    assert "X-Anthropic-Key" not in req.headers


def test_consultar_401(mock, cliente):
    mock.get("/v1/jobs/abc").respond(401, json={"detail": "no"})
    with pytest.raises(ClaveYtiInvalida):
        cliente.consultar("abc")


def test_consultar_404(mock, cliente):
    mock.get("/v1/jobs/abc").respond(404, json={"detail": "no"})
    with pytest.raises(TrabajoNoEncontrado) as e:
        cliente.consultar("abc")
    assert e.value.http == 404


def test_consultar_sin_estado(mock, cliente):
    mock.get("/v1/jobs/abc").respond(200, json={"x": 1})
    with pytest.raises(ContratoIncompatible):
        cliente.consultar("abc")


def test_consultar_5xx(mock, cliente):
    mock.get("/v1/jobs/abc").respond(500, text="boom")
    with pytest.raises(ServicioNoDisponible):
        cliente.consultar("abc")


def test_despertar_plazo_90(mock, cliente):
    ruta = mock.get("/health/vivo").respond(200, json={})
    cliente.despertar()
    timeout = ruta.calls.last.request.extensions["timeout"]
    assert timeout["read"] == 90.0
    assert timeout["connect"] == 90.0


def test_despertar_falla(mock, cliente):
    mock.get("/health/vivo").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(ServicioNoDisponible):
        cliente.despertar()


def test_despertar_503(mock, cliente):
    mock.get("/health/vivo").respond(503)
    with pytest.raises(ServicioNoDisponible):
        cliente.despertar()


@pytest.mark.parametrize(
    "version,esperado",
    [
        ("0.2.2", True),
        ("0.2.10", True),
        ("0.2.1", False),
        ("0.3.0", False),
        ("1.0.0", False),
        ("x", False),
    ],
)
def test_version_compatible(version, esperado):
    assert version_compatible(version) is esperado


def test_version_api(mock, cliente):
    mock.get("/openapi.json").respond(200, json={"info": {"version": "0.2.3"}})
    assert cliente.version_api() == "0.2.3"


@pytest.mark.parametrize("cuerpo", [[], {"info": []}, {"info": {"version": 3}}])
def test_version_api_forma_inesperada(mock, cliente, cuerpo):
    mock.get("/openapi.json").respond(200, json=cuerpo)
    assert cliente.version_api() is None


def test_version_api_red(mock, cliente):
    mock.get("/openapi.json").mock(side_effect=httpx.ConnectError("x"))
    assert cliente.version_api() is None


T0 = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)


def test_contrato_cache_24h(mock, cliente, tmp_path):
    ruta = mock.get("/openapi.json").respond(200, json={"info": {"version": "0.2.2"}})
    cache = tmp_path / "contrato.json"
    assert contrato_compatible(cliente, cache, T0) is True
    assert contrato_compatible(cliente, cache, T0 + timedelta(hours=1)) is True
    assert ruta.call_count == 1
    assert contrato_compatible(cliente, cache, T0 + timedelta(hours=25)) is True
    assert ruta.call_count == 2


def test_contrato_incompatible(mock, cliente, tmp_path):
    mock.get("/openapi.json").respond(200, json={"info": {"version": "0.3.0"}})
    cache = tmp_path / "contrato.json"
    assert contrato_compatible(cliente, cache, T0) is False
    assert contrato_compatible(cliente, cache, T0 + timedelta(hours=1)) is False


def test_contrato_sin_openapi(mock, cliente, tmp_path):
    mock.get("/openapi.json").respond(404)
    assert contrato_compatible(cliente, tmp_path / "contrato.json", T0) is True


def test_contrato_cache_corrupta(mock, cliente, tmp_path):
    mock.get("/openapi.json").respond(200, json={"info": {"version": "0.2.2"}})
    cache = tmp_path / "contrato.json"
    cache.write_text("no es json", encoding="utf-8")
    assert contrato_compatible(cliente, cache, T0) is True


def test_mensajes_sin_claves(mock, cliente):
    casos = [
        (401, {"detail": f"clave {CLAVE_YTI}"}),
        (400, {"detail": {"codigo": "CLAVE_MAL_FORMADA", "mensaje": "mal"}}),
        (422, {"detail": {"codigo": "TESTIGO_INVALIDO", "mensaje": "vacío"}}),
        (422, {"detail": [{"msg": "x"}]}),
        (429, {"detail": "tope"}),
        (503, {"detail": {"codigo": "BD_NO_DISPONIBLE", "mensaje": "bd"}}),
        (502, None),
    ]
    for status, cuerpo in casos:
        mock.reset()
        if cuerpo is None:
            mock.post("/v1/jobs").respond(status, text="<html/>")
        else:
            mock.post("/v1/jobs").respond(status, json=cuerpo)
        with pytest.raises(ErrorApi) as e:
            crear(cliente)
        for clave in CLAVES:
            assert clave not in str(e.value)
            assert clave not in e.value.mensaje
    for exc, tipo in [
        (httpx.ReadTimeout("t"), RespuestaPerdida),
        (httpx.ConnectTimeout("t"), ServicioNoDisponible),
    ]:
        mock.reset()
        mock.post("/v1/jobs").mock(side_effect=exc)
        with pytest.raises(tipo) as e:
            crear(cliente)
        for clave in CLAVES:
            assert clave not in str(e.value)
    mock.reset()
    mock.post("/v1/jobs").respond(202, json={})
    with pytest.raises(ContratoIncompatible) as e:
        crear(cliente)
    for clave in CLAVES:
        assert clave not in str(e.value)
    mock.reset()
    mock.get("/v1/jobs/abc").respond(404, json={"detail": "x"})
    with pytest.raises(TrabajoNoEncontrado) as e:
        cliente.consultar("abc")
    for clave in CLAVES:
        assert clave not in str(e.value)
