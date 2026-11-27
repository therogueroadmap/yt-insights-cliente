import dataclasses
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from yti_cliente.cola import Cola, iso
from yti_cliente.config import Config
from yti_cliente.ejecutor import (
    ESTADOS_FINALES,
    ResumenActivacion,
    activar,
    bajar_resultado,
    crear_contexto,
    esperar,
    hay_activos,
)
from yti_cliente.testigo import InfoVideo, ResultadoTestigo

BASE = "https://api.test"
VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"
CLAVE_YTI = "clave-yti-secreta-0001"
CLAVE_GEMINI = "gemini-secreta-" + "g" * 20
CLAVE_ANTHROPIC = "sk-ant-secreta-" + "a" * 20
AHORA = datetime(2026, 11, 18, 14, 25, 30, tzinfo=timezone.utc)
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHola\n"
OBJETIVO = "Migrar a microservicios"

ANALISIS = {
    "resumen": "Un resumen.",
    "puntos_clave": [{"segundo": 65, "texto": "Punto uno"}],
}


class Reloj:
    def __init__(self):
        self.ahora = AHORA

    def __call__(self):
        return self.ahora


def vista_completada(
    modo="analisis", verificacion=None, metadatos=None, uso=None, id_remoto="r1", **extra
):
    if verificacion is None:
        verificacion = {"estado": "verificado", "coincidencia_pct": 87}
    if metadatos is None:
        metadatos = {"titulo": "Cómo migrar", "canal": "Canal X", "duracion_s": 600}
    if uso is None:
        uso = {"costo_usd": {"total": 0.0123}}
    vista = {
        "id": id_remoto, "estado": "completado", "video_id": VID, "modo": modo,
        "modelo": "modelo-x", "intentos": 1, "metodo_transcripcion": "captions",
        "fuente_testigo": "cliente", "objetivo": OBJETIVO if modo == "analisis" else None,
        "resultado": {
            "metadatos": metadatos, "transcripcion_md": "# Transcripción\n\nHola.\n",
            "analisis": ANALISIS if modo == "analisis" else None, "verificacion": verificacion,
        },
        "uso": uso,
    }
    vista.update(extra)
    return vista


class Entorno:
    def __init__(self, ctx, router, post, reloj, cfg):
        self.ctx, self.router, self.post, self.reloj, self.cfg = ctx, router, post, reloj, cfg

    def sembrar(self, estado="enviado", modo="analisis", id_remoto="r1", **extra):
        datos = {
            "url": URL, "video_id": VID, "modo": modo,
            "objetivo": OBJETIVO if modo == "analisis" else None,
            "objetivo_normalizado": OBJETIVO.lower() if modo == "analisis" else "",
            "testigo_estado": "sin_subtitulos",
        }
        datos.update(extra)
        trabajo = self.ctx.cola.crear(datos)
        cambios = {"estado": estado}
        if estado == "enviado":
            cambios.update(id_remoto=id_remoto, estado_remoto="pendiente")
        return self.ctx.cola.liberar(trabajo, **cambios)

    def get(self, id_remoto="r1", **kw):
        return self.router.get(f"/v1/jobs/{id_remoto}").respond(**kw)

    def leer(self, trabajo):
        return self.ctx.cola.leer(trabajo["id_local"])

    @property
    def raiz(self):
        return self.cfg.biblioteca


def _cfg(tmp_path):
    return Config(
        api_url=BASE, biblioteca=tmp_path / "bib", clave_yti=CLAVE_YTI,
        clave_gemini=CLAVE_GEMINI, clave_anthropic=CLAVE_ANTHROPIC, origen={},
    )


@pytest.fixture
def env(tmp_path):
    cfg = _cfg(tmp_path)
    reloj = Reloj()
    ctx = crear_contexto(
        cfg, reloj=reloj,
        obtener_testigo=lambda url: ResultadoTestigo(
            "obtenido", VTT, "es", None, InfoVideo("Un título", "Un canal", 912)
        ),
    )
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.get("/openapi.json").respond(json={"info": {"version": "0.2.2"}})
        router.get("/health/vivo").respond(200)
        post = router.post("/v1/jobs").respond(202, json={"id": "remoto-1"})
        yield Entorno(ctx, router, post, reloj, cfg)


def _detalle(codigo, mensaje="m"):
    return {"detail": {"codigo": codigo, "mensaje": mensaje}}


# --- activar: envío ---


def test_activar_envia_por_enviar_y_retenido_vencido(env):
    a = env.sembrar("por_enviar")
    b = env.sembrar("retenido", enviar_despues=iso(AHORA - timedelta(minutes=1)))
    c = env.sembrar("retenido", enviar_despues=iso(AHORA + timedelta(hours=1)))
    env.router.get("/v1/jobs/remoto-1").respond(json={"estado": "pendiente", "intentos": 0})
    resumen = activar(env.ctx)
    assert isinstance(resumen, ResumenActivacion)
    assert len(env.post.calls) == 2
    assert {t["id_local"] for t in resumen.enviados} == {a["id_local"], b["id_local"]}
    assert env.leer(c)["estado"] == "retenido"
    assert env.leer(a)["estado"] == "enviado"


def test_activar_errorlocal_no_aborta_a_los_demas(env):
    env.sembrar("por_enviar", modo="analisis")
    env.sembrar("por_enviar", modo="transcripcion")
    env.ctx.cfg = dataclasses.replace(env.ctx.cfg, clave_anthropic=None)
    env.router.get("/v1/jobs/remoto-1").respond(json={"estado": "pendiente", "intentos": 0})
    resumen = activar(env.ctx)
    assert len(env.post.calls) == 1
    assert len(resumen.enviados) == 1
    assert [e["codigo"] for e in resumen.errores] == ["FALTA_CLAVE"]
    assert CLAVE_ANTHROPIC not in json.dumps(resumen.errores)


def test_hay_activos(env):
    assert hay_activos(env.ctx) is False
    env.sembrar("enviado")
    assert hay_activos(env.ctx) is True


# --- sondeo ---


def test_sondeo_procesando(env):
    t = env.sembrar("enviado")
    env.get(json={"estado": "procesando", "intentos": 2})
    activar(env.ctx)
    actual = env.leer(t)
    assert actual["estado"] == "enviado"
    assert actual["estado_remoto"] == "procesando"
    assert actual["intentos_remotos"] == 2


@pytest.mark.parametrize(
    "codigo,contiene",
    [
        ("GEMINI_AUTH", "--clave gemini"),
        ("CLAUDE_AUTH", "Anthropic"),
        ("SIN_API_KEY_CLAUDE", "Anthropic"),
        ("VIDEO_PRIVADO", "El video es privado"),
    ],
)
def test_errores_de_trabajo(env, codigo, contiene):
    t = env.sembrar("enviado")
    env.get(json={"estado": "error", "intentos": 1,
                  "error": {"codigo": codigo, "mensaje": "El video es privado"}})
    resumen = activar(env.ctx)
    actual = env.leer(t)
    assert actual["estado"] == "error"
    assert actual["error"]["codigo"] == codigo
    assert contiene in actual["error"]["mensaje"]
    assert resumen.errores[0]["id_local"] == t["id_local"]


def test_error_remoto_borra_testigo(env):
    t = env.sembrar("enviado")
    env.ctx.cola.guardar_testigo(t["id_local"], VTT)
    env.get(json={"estado": "error", "error": {"codigo": "VIDEO_PRIVADO", "mensaje": "x"}})
    activar(env.ctx)
    assert env.ctx.cola.leer_testigo(t["id_local"]) is None


def test_sondeo_401_sin_cambio(env):
    t = env.sembrar("enviado")
    env.get(status_code=401)
    resumen = activar(env.ctx)
    assert env.leer(t)["estado"] == "enviado"
    assert resumen.avisos


def test_sondeo_404_perdido(env):
    t = env.sembrar("enviado")
    env.ctx.cola.guardar_testigo(t["id_local"], VTT)
    env.get(status_code=404)
    resumen = activar(env.ctx)
    assert env.leer(t)["estado"] == "perdido"
    assert env.ctx.cola.leer_testigo(t["id_local"]) is None
    assert resumen.avisos


def test_sondeo_503_sin_cambio(env):
    t = env.sembrar("enviado")
    env.get(status_code=503)
    resumen = activar(env.ctx)
    assert env.leer(t)["estado"] == "enviado"
    assert resumen.errores == []


def test_sondeo_error_de_red_sin_cambio(env):
    t = env.sembrar("enviado")
    env.router.get("/v1/jobs/r1").mock(side_effect=httpx.ConnectError("sin red"))
    activar(env.ctx)
    assert env.leer(t)["estado"] == "enviado"


def test_respuesta_descartada_si_cambio(env):
    t = env.sembrar("enviado")

    def cambiar_y_responder(request):
        otra = Cola(env.raiz, reloj=env.reloj)
        otra.actualizar_si(t["id_local"], {"estado": "enviado"}, {"id_remoto": "otro"})
        return httpx.Response(200, json={"estado": "procesando", "intentos": 5})

    env.router.get("/v1/jobs/r1").mock(side_effect=cambiar_y_responder)
    activar(env.ctx)
    actual = env.leer(t)
    assert actual["id_remoto"] == "otro"
    assert actual["estado_remoto"] == "pendiente"
    assert actual["intentos_remotos"] == 0


def test_claves_no_disponibles_un_reenvio(env):
    t = env.sembrar("por_enviar", testigo_estado="obtenido", testigo_idioma="es")
    env.ctx.cola.guardar_testigo(t["id_local"], VTT)
    env.router.get("/v1/jobs/remoto-1").respond(
        json={"estado": "error", "error": {"codigo": "CLAVES_NO_DISPONIBLES", "mensaje": "x"}}
    )
    # 1.ª activación: envía y el sondeo recibe CLAVES_NO_DISPONIBLES
    activar(env.ctx)
    actual = env.leer(t)
    assert actual["estado"] == "por_enviar"
    assert actual["reenvios_claves"] == 1
    assert actual["id_remoto"] is None
    assert env.ctx.cola.leer_testigo(t["id_local"]) == VTT

    # 2.ª activación: reenvía con el testigo guardado y obtiene otro id_remoto
    env.post.respond(202, json={"id": "remoto-2"})
    env.router.get("/v1/jobs/remoto-2").respond(json={"estado": "procesando", "intentos": 1})
    activar(env.ctx)
    assert len(env.post.calls) == 2
    cuerpo = json.loads(env.post.calls[-1].request.content)
    assert cuerpo["testigo"]["contenido"] == VTT
    assert cuerpo["testigo"]["idioma"] == "es"
    actual = env.leer(t)
    assert actual["id_remoto"] == "remoto-2"
    assert actual["reenvios_claves"] == 1

    # un segundo CLAVES_NO_DISPONIBLE deja el trabajo en error
    env.router.get("/v1/jobs/remoto-2").respond(
        json={"estado": "error", "error": {"codigo": "CLAVES_NO_DISPONIBLES", "mensaje": "x"}}
    )
    activar(env.ctx)
    actual = Cola(env.raiz, reloj=env.reloj).leer(t["id_local"])
    assert actual["estado"] == "error"
    assert actual["reenvios_claves"] == 1
    assert "a mano" in actual["error"]["mensaje"]
    assert env.ctx.cola.leer_testigo(t["id_local"]) is None


# --- baja ---


def test_baja_analisis(env):
    t = env.sembrar("enviado")
    env.ctx.cola.guardar_testigo(t["id_local"], VTT)
    env.get(json=vista_completada("analisis"))
    resumen = activar(env.ctx)
    carpeta = env.raiz / "videos" / f"{VID}-como-migrar"
    trans = (carpeta / "transcripcion.md").read_text(encoding="utf-8")
    assert "fuente_testigo" in trans and "cliente" in trans
    assert "coincidencia_pct: 87" in trans
    assert 'trabajo: "r1"' in trans
    analisis = (carpeta / "analisis-migrar-a-microservicios.md").read_text(encoding="utf-8")
    assert "costo_usd: 0.0123" in analisis
    assert "verificado 87 %" in analisis
    indice = (env.raiz / "indice.md").read_text(encoding="utf-8")
    assert VID in indice and "analisis-migrar-a-microservicios.md" in indice
    actual = env.leer(t)
    assert actual["estado"] == "en_biblioteca"
    assert actual["uso"] == {"costo_usd": {"total": 0.0123}}
    assert env.ctx.cola.leer_testigo(t["id_local"]) is None
    assert len(resumen.bajados) == 1


def test_baja_transcripcion_con_nulos(env):
    t = env.sembrar("enviado", modo="transcripcion")
    vista = vista_completada(
        "transcripcion", verificacion={"estado": "no_aplica", "coincidencia_pct": None},
        metadatos={"titulo": "Un video", "canal": None, "duracion_s": None},
    )
    vista["resultado"]["analisis"] = None
    vista["resultado"]["verificacion"] = None
    vista["uso"] = None
    env.get(json=vista)
    activar(env.ctx)
    carpeta = env.raiz / "videos" / f"{VID}-un-video"
    trans = (carpeta / "transcripcion.md").read_text(encoding="utf-8")
    assert 'duracion: "desconocida"' in trans
    assert 'canal: "desconocido"' in trans
    assert list(carpeta.glob("analisis-*.md")) == []
    actual = env.leer(t)
    assert actual["estado"] == "en_biblioteca"
    assert actual["uso"] is None


def test_baja_analisis_nulo_en_modo_analisis_no_escribe_analisis(env):
    env.sembrar("enviado", modo="analisis")
    vista = vista_completada("analisis")
    vista["resultado"]["analisis"] = None
    env.get(json=vista)
    activar(env.ctx)
    assert list((env.raiz / "videos").glob("*/analisis-*.md")) == []


def test_titulo_respaldo(env):
    t = env.sembrar("enviado", modo="transcripcion", titulo_local="Título local")
    env.get(json=vista_completada("transcripcion", metadatos={"titulo": "", "duracion_s": 5}))
    activar(env.ctx)
    assert (env.raiz / "videos" / f"{VID}-titulo-local" / "transcripcion.md").is_file()
    assert env.leer(t)["estado"] == "en_biblioteca"


def test_titulo_sin_ninguno_usa_video_id(env):
    env.sembrar("enviado", modo="transcripcion")
    env.get(json=vista_completada("transcripcion", metadatos={}))
    activar(env.ctx)
    carpeta = env.raiz / "videos" / VID
    trans = (carpeta / "transcripcion.md").read_text(encoding="utf-8")
    assert f'titulo: "{VID}"' in trans
    assert 'canal: "desconocido"' in trans


def test_duracion_local_tiene_prioridad(env):
    env.sembrar("enviado", modo="transcripcion", duracion_s=3700)
    env.get(json=vista_completada("transcripcion", metadatos={"titulo": "T", "duracion_s": 60}))
    activar(env.ctx)
    trans = (env.raiz / "videos" / f"{VID}-t" / "transcripcion.md").read_text(encoding="utf-8")
    assert "1:01:40" in trans


def test_baja_idempotente(env):
    t = env.sembrar("enviado")
    vista = vista_completada("analisis")
    bajar_resultado(env.ctx, env.leer(t), vista)
    archivos1 = sorted(p.name for p in env.raiz.rglob("*.md"))
    indice1 = (env.raiz / "indice.md").read_text(encoding="utf-8")
    final = bajar_resultado(env.ctx, env.leer(t), vista)
    assert final["estado"] == "en_biblioteca"
    assert sorted(p.name for p in env.raiz.rglob("*.md")) == archivos1
    indice2 = (env.raiz / "indice.md").read_text(encoding="utf-8")
    assert indice2 == indice1
    assert indice2.count(f"[{VID}]") == 1


def test_baja_con_proyecto_por_id_no_toca_pertenencia(env):
    p = env.ctx.proyectos.crear("Mi proyecto")
    env.sembrar("enviado", proyecto=p.id)
    env.get(json=vista_completada("analisis"))
    activar(env.ctx)
    assert env.ctx.proyectos.resolver(p.id).videos == []
    assert (env.raiz / "proyectos" / "mi-proyecto.md").is_file()


def test_baja_respeta_un_quitar_hecho_en_cola(env):
    p = env.ctx.proyectos.crear("P")
    env.ctx.proyectos.agregar_video("P", VID)
    env.ctx.proyectos.quitar_video("P", VID)
    env.sembrar("enviado", proyecto=p.id)
    env.get(json=vista_completada("analisis"))
    activar(env.ctx)
    assert env.ctx.proyectos.resolver("P").videos == []


def test_baja_con_nombre_legado_resuelve_o_crea_y_enlaza(env):
    env.sembrar("enviado", proyecto="Mi proyecto")
    env.get(json=vista_completada("analisis"))
    activar(env.ctx)
    p = env.ctx.proyectos.resolver("Mi proyecto")
    assert [v["video_id"] for v in p.videos] == [VID]
    texto = (env.raiz / "proyectos" / "mi-proyecto.md").read_text(encoding="utf-8")
    assert f"../videos/{VID}-como-migrar/" in texto


def test_baja_con_proyecto_borrado_no_lo_recrea(env, caplog):
    p = env.ctx.proyectos.crear("P")
    env.ctx.proyectos.borrar("P")
    t = env.sembrar("enviado", proyecto=p.id)
    env.get(json=vista_completada("analisis"))
    activar(env.ctx)
    assert env.ctx.proyectos.listar() == [] and env.leer(t)["estado"] == "en_biblioteca"
    assert "P" in caplog.text or p.id in caplog.text


def test_fallo_al_regenerar_vistas_no_pierde_la_operacion(env, monkeypatch):
    from yti_cliente import ejecutor

    def falla(_proyectos):
        raise OSError("disco lleno")

    monkeypatch.setattr(ejecutor, "regenerar_vistas", falla)
    proyecto, aviso = ejecutor.con_proyectos(env.ctx, lambda p: p.crear("Z"))
    assert env.ctx.proyectos.resolver("Z").id == proyecto.id
    assert "se corregirán" in aviso


def test_completado_sin_transcripcion(env):
    t = env.sembrar("enviado")
    vista = vista_completada("analisis")
    del vista["resultado"]["transcripcion_md"]
    env.get(json=vista)
    resumen = activar(env.ctx)
    actual = env.leer(t)
    assert actual["estado"] == "error"
    assert actual["error"]["codigo"] == "CONTRATO_INCOMPATIBLE"
    assert actual["id_remoto"] == "r1"
    assert resumen.errores[0]["codigo"] == "CONTRATO_INCOMPATIBLE"
    assert not (env.raiz / "videos").exists()


# --- esperar ---


def test_esperar_intervalos(env):
    t = env.sembrar("enviado")
    pausas = []

    def dormir(s):
        pausas.append(s)
        env.reloj.ahora += timedelta(seconds=s)
        if len(pausas) == 10:
            env.router.get("/v1/jobs/r1").respond(json=vista_completada("analisis"))

    env.get(json={"estado": "procesando", "intentos": 1})
    res = esperar(env.ctx, t["id_local"], dormir=dormir)
    assert pausas == [15] * 8 + [60, 60]
    assert res[0]["estado"] == "en_biblioteca"
    assert res[0]["estado"] in ESTADOS_FINALES


def test_esperar_tope_sin_excepcion(env):
    t = env.sembrar("enviado")
    env.get(json={"estado": "procesando", "intentos": 1})
    pausas = []

    def dormir(s):
        pausas.append(s)
        env.reloj.ahora += timedelta(seconds=s)

    res = esperar(env.ctx, t["id_local"], tope_min=3, dormir=dormir)
    assert pausas == [15] * 8 + [60]
    assert res[0]["estado"] == "enviado"


def test_esperar_todos_los_activos(env):
    a = env.sembrar("enviado", id_remoto="r1")
    b = env.sembrar("enviado", id_remoto="r2")
    env.get("r1", json=vista_completada("analisis", id_remoto="r1"))
    env.get("r2", json={"estado": "error", "error": {"codigo": "VIDEO_PRIVADO", "mensaje": "x"}})
    res = esperar(env.ctx, dormir=lambda s: pytest.fail("no debía dormir"))
    assert {t["id_local"]: t["estado"] for t in res} == {
        a["id_local"]: "en_biblioteca", b["id_local"]: "error"
    }


def test_esperar_sin_activos(env):
    assert esperar(env.ctx, dormir=lambda s: pytest.fail("no debía dormir")) == []


# --- fixes de la revisión final ---


def test_copia_de_conflicto_no_reenvia(env):
    t = env.sembrar("por_enviar")
    env.router.get("/v1/jobs/remoto-1").respond(json={"estado": "pendiente", "intentos": 0})
    carpeta = env.raiz / ".yti" / "trabajos"
    copia = carpeta / f"{t['id_local']} (conflicted copy).json"
    copia.write_text((carpeta / f"{t['id_local']}.json").read_text(encoding="utf-8"), encoding="utf-8")
    for _ in range(4):
        activar(env.ctx)
    assert len(env.post.calls) == 1
    assert env.leer(t)["id_remoto"] == "remoto-1"


def test_activar_aisla_fallo_de_un_trabajo(env, monkeypatch):
    from yti_cliente import ejecutor

    a = env.sembrar("por_enviar")
    b = env.sembrar("por_enviar")
    original = ejecutor.enviar

    def falla_el_primero(ctx, trabajo):
        if trabajo["id_local"] == a["id_local"]:
            raise OSError("disco")
        return original(ctx, trabajo)

    monkeypatch.setattr(ejecutor, "enviar", falla_el_primero)
    env.router.get("/v1/jobs/remoto-1").respond(json={"estado": "pendiente", "intentos": 0})
    resumen = activar(env.ctx)
    assert env.leer(b)["estado"] == "enviado"
    assert any(e["id_local"] == a["id_local"] for e in resumen.errores)


def test_activar_aisla_fallo_de_sondeo(env, monkeypatch):
    from yti_cliente import ejecutor

    a = env.sembrar("enviado", id_remoto="r1")
    b = env.sembrar("enviado", id_remoto="r2")
    env.get(id_remoto="r2", json=vista_completada("analisis", id_remoto="r2"))
    original = ejecutor._sondear

    def falla(ctx, api, trabajo, resumen):
        if trabajo["id_local"] == a["id_local"]:
            raise TypeError("malformado")
        return original(ctx, api, trabajo, resumen)

    monkeypatch.setattr(ejecutor, "_sondear", falla)
    resumen = activar(env.ctx)
    assert env.leer(b)["estado"] == "en_biblioteca"
    assert any(e["id_local"] == a["id_local"] for e in resumen.errores)


def test_vista_sin_estado_es_contrato_incompatible(env):
    t = env.sembrar("enviado")
    env.get(json={"id": "r1"})
    resumen = activar(env.ctx)
    actual = env.leer(t)
    assert actual["estado"] == "error"
    assert actual["error"]["codigo"] == "CONTRATO_INCOMPATIBLE"
    assert actual["id_remoto"] == "r1"
    assert resumen.errores[0]["codigo"] == "CONTRATO_INCOMPATIBLE"


def test_analisis_nulo_en_modo_analisis_avisa(env):
    t = env.sembrar("enviado")
    vista = vista_completada("analisis")
    vista["resultado"]["analisis"] = None
    env.get(json=vista)
    resumen = activar(env.ctx)
    actual = env.leer(t)
    assert actual["estado"] == "en_biblioteca"
    assert "no devolvió el análisis" in actual["aviso"]
    assert any("no devolvió el análisis" in a for a in resumen.avisos)


def test_excepcion_generica_antes_del_post_libera_el_trabajo(env, monkeypatch):
    from yti_cliente import ejecutor

    a = env.sembrar("por_enviar")
    b = env.sembrar("por_enviar")
    original = ejecutor.enviar

    def falla_el_primero(ctx, trabajo):
        if trabajo["id_local"] == a["id_local"]:
            raise RuntimeError("fallo inesperado antes del POST")
        return original(ctx, trabajo)

    monkeypatch.setattr(ejecutor, "enviar", falla_el_primero)
    env.router.get("/v1/jobs/remoto-1").respond(json={"estado": "pendiente", "intentos": 0})
    resumen = activar(env.ctx)
    liberado = env.leer(a)
    assert liberado["estado"] == "por_enviar"
    assert liberado["tomado_por"] is None and liberado["tomado_en"] is None
    assert env.leer(b)["estado"] == "enviado"
    assert any(e["id_local"] == a["id_local"] for e in resumen.errores)


def test_excepcion_con_id_remoto_no_libera_el_trabajo(env, monkeypatch):
    from yti_cliente import ejecutor

    a = env.sembrar("por_enviar")
    def falla_con_remoto(ctx, trabajo):
        ctx.cola.actualizar_si(
            trabajo["id_local"],
            {"estado": "enviando", "tomado_por": ctx.cola.id_proceso},
            {"id_remoto": "remoto-ya-creado"},
        )
        raise RuntimeError("fallo tras el POST")

    monkeypatch.setattr(ejecutor, "enviar", falla_con_remoto)
    resumen = activar(env.ctx)
    guardado = env.leer(a)
    assert guardado["id_remoto"] == "remoto-ya-creado"
    assert guardado["estado"] == "enviando"
    assert any(e["id_local"] == a["id_local"] for e in resumen.errores)
