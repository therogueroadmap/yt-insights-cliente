"""Ejecutor: registra un trabajo en la cola local y lo envía a la API (§9.2, §10.1)."""

import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar

import httpx
import platformdirs

from yti_cliente import testigo as _testigo
from yti_cliente.api import (
    ClaveYtiInvalida,
    ClienteApi,
    ContratoIncompatible,
    ErrorApi,
    RespuestaPerdida,
    ServicioNoDisponible,
    TestigoInvalido,
    TopeDiario,
    TrabajoNoEncontrado,
    contrato_compatible,
)
from yti_cliente.biblioteca import Biblioteca, normalizar_objetivo, texto_verificacion
from yti_cliente.cola import Cola, ahora_utc, desde_iso, iso
from yti_cliente.config import Config, ErrorConfig, validar_clave_proveedor, validar_idioma
from yti_cliente.errores import ErrorLocal
from yti_cliente.limites import MAX_OBJETIVO
from yti_cliente.proyectos import Proyectos, es_id_proyecto
from yti_cliente.proyectos_migracion import migrar_antiguos_contando
from yti_cliente.proyectos_vista import regenerar_vistas
from yti_cliente.render_md import analisis_a_markdown, marca_tiempo
from yti_cliente.testigo import ResultadoTestigo, SinConexion
from yti_cliente.youtube import UrlNoAdmitida, extraer_video_id

log = logging.getLogger(__name__)

_ESTIMACION_GENERICA = "entre 2 y 10 minutos según la duración"
_AVISO_VERSION = (
    "la API usa una versión no compatible; actualiza con `uv tool upgrade yt-insights-cliente`"
)
_REPORTAR = "es un fallo del cliente; reporta el problema"


class _TomaPerdida(Exception):
    """Otro proceso se quedó con el trabajo mientras este lo enviaba."""


@dataclass
class Contexto:
    cfg: Config
    cola: Cola
    biblioteca: Biblioteca
    reloj: Callable[[], datetime]
    obtener_testigo: Callable[[str], ResultadoTestigo]
    transport: httpx.BaseTransport | None
    ruta_cache_version: Path
    proyectos: Proyectos

    def api(self) -> ClienteApi:
        if _falta(self.cfg.clave_yti):
            raise ErrorLocal(
                "FALTA_CLAVE",
                "Falta la clave de yt-insights; configúrala con `yti config --clave yt-insights`.",
            )
        return ClienteApi(self.cfg.api_url, self.cfg.clave_yti, transport=self.transport)


@dataclass
class ResultadoEncolar:
    duplicado: bool
    mensaje: str
    trabajo: dict | None
    estimacion: str
    aviso: str | None = None


def crear_contexto(
    cfg: Config,
    *,
    reloj: Callable[[], datetime] | None = None,
    obtener_testigo: Callable[[str], ResultadoTestigo] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Contexto:
    reloj = reloj or ahora_utc
    cola = Cola(cfg.biblioteca, reloj=reloj)
    biblioteca = Biblioteca(cfg.biblioteca)
    return Contexto(
        cfg=cfg,
        cola=cola,
        biblioteca=biblioteca,
        reloj=reloj,
        obtener_testigo=obtener_testigo or _testigo.obtener_testigo,
        transport=transport,
        ruta_cache_version=platformdirs.user_state_path("yt-insights-cliente", appauthor=False)
        / "version.json",
        proyectos=Proyectos(biblioteca, cola),
    )


def estimacion(duracion_s: int | None) -> str:
    if duracion_s is None:
        return _ESTIMACION_GENERICA
    return f"unos {int(2 + duracion_s / 300 + 0.5)} minutos"


# --- registrar ---


def _falta(clave: str | None) -> bool:
    """Una clave ausente, vacía o solo con espacios cuenta como no configurada."""
    return not (clave or "").strip()


_OPCION_CONFIGURAR = "configúrala con `yti config --clave anthropic`"
_OPCION_CLI = "pide solo la transcripción con `yti encolar <url> --solo-transcripcion`"
_OPCION_MCP = (
    'pide solo la transcripción con `encolar_video(..., solo_transcripcion=True)` y analízala '
    'en esta sesión con `leer_resultado(video, contenido="transcripcion")`'
)


def mensaje_falta_anthropic(canal: str | None = None) -> str:
    """Error de análisis sin clave de Anthropic con las opciones del canal ("cli", "mcp" o ambos si es None).

    Nunca se cambia la solicitud a solo transcripción en silencio: el usuario elige.
    """
    if canal == "cli":
        opciones = f"{_OPCION_CONFIGURAR} o {_OPCION_CLI}"
    elif canal == "mcp":
        opciones = f"{_OPCION_CONFIGURAR} o {_OPCION_MCP}"
    else:
        opciones = (
            f"{_OPCION_CONFIGURAR}; o pide solo la transcripción "
            "(`--solo-transcripcion` en la terminal, `solo_transcripcion=True` en el MCP) y analízala "
            "con tu asistente"
        )
    return f"Falta la clave de Anthropic, necesaria para el análisis. Opciones: {opciones}."


def _verificar_claves(cfg: Config, modo: str, canal: str | None = None) -> None:
    if _falta(cfg.clave_yti):
        raise ErrorLocal(
            "FALTA_CLAVE",
            "Falta la clave de yt-insights; configúrala con `yti config --clave yt-insights`.",
        )
    if _falta(cfg.clave_gemini):
        raise ErrorLocal(
            "FALTA_CLAVE",
            "Falta la clave de Gemini; configúrala con `yti config --clave gemini`.",
        )
    if modo == "analisis" and _falta(cfg.clave_anthropic):
        raise ErrorLocal("FALTA_CLAVE", mensaje_falta_anthropic(canal))
    proveedores = [("gemini", cfg.clave_gemini)]
    if modo == "analisis":
        proveedores.append(("anthropic", cfg.clave_anthropic))
    for nombre, valor in proveedores:
        if not validar_clave_proveedor(valor):
            raise ErrorLocal(
                "CLAVE_MAL_FORMADA",
                f"La clave de {nombre} tiene un formato inválido; "
                f"vuelve a configurarla con `yti config --clave {nombre}`.",
            )


def _aviso_duplicado(texto: str, url: str, canal: str, *, en_cola: bool = False) -> str:
    """Mensaje de duplicado con la salida que admite el canal.

    Desde el MCP, `forzar` no salta un trabajo igual que ya está en la cola: ahí no se sugiere."""
    if canal != "mcp":
        return f"{texto}. Para enviarlo igualmente: yti encolar {url} --forzar"
    if en_cola:
        return f"{texto}. Consulta el avance con ver_cola."
    return (
        f"{texto}. Si el usuario pide repetirlo, vuelve a llamar a encolar_video con forzar=true; "
        "el resultado nuevo reemplaza al anterior."
    )


def _aviso_analisis_previos(ctx: Contexto, video_id: str, normalizado: str, canal: str) -> str | None:
    """Lista los objetivos ya analizados del video distintos del pedido; None si no hay."""
    previos = [
        o for o in ctx.biblioteca.objetivos_analisis(video_id) if normalizar_objetivo(o) != normalizado
    ]
    if not previos:
        return None
    lista = ", ".join(f"«{o}»" if o else "general" for o in previos)
    como = "con leer_resultado" if canal == "mcp" else "en la biblioteca"
    return (
        f"El video ya tiene análisis con estos objetivos: {lista}. "
        f"Si alguno sirve, léelo {como}; el nuevo análisis reutiliza la transcripción, "
        "así que normalmente solo cuesta el análisis de Claude."
    )


T = TypeVar("T")

_AVISO_VISTAS = "No se pudieron regenerar las vistas de proyectos; se corregirán en la próxima operación."


def preparar_proyectos(ctx: Contexto) -> Proyectos:
    """Migra los `.md` antiguos y devuelve `ctx.proyectos`. El candado lo sostiene quien llama."""
    migrados = 0
    try:
        avisos, migrados = migrar_antiguos_contando(ctx.proyectos)
        for aviso in avisos:
            log.warning("migración de proyectos: %s", aviso)
    except OSError as e:
        log.warning("no se pudieron migrar los proyectos antiguos: %s", e)
    if migrados:
        actualizar_vistas(ctx)
    return ctx.proyectos


def actualizar_vistas(ctx: Contexto) -> str | None:
    """Regenera las vistas; ante un fallo de disco registra y devuelve un aviso."""
    try:
        regenerar_vistas(ctx.proyectos)
    except Exception as e:  # noqa: BLE001 - la vista nunca tumba la operación
        log.warning("no se pudieron regenerar las vistas de proyectos: %s", getattr(e, "mensaje", e))
        return _AVISO_VISTAS
    return None


def con_proyectos(ctx: Contexto, accion: Callable[[Proyectos], T]) -> tuple[T, str | None]:
    """Ejecuta `accion` con el candado, tras migrar, y regenera las vistas al final."""
    with ctx.cola.candado():
        resultado = accion(preparar_proyectos(ctx))
        return resultado, actualizar_vistas(ctx)


def registrar(
    ctx: Contexto,
    url: str,
    *,
    objetivo: str | None = None,
    solo_transcripcion: bool = False,
    sin_testigo: bool = False,
    proyecto: str | None = None,
    forzar: bool = False,
    idioma: str | None = None,
    canal: str = "cli",
) -> ResultadoEncolar:
    try:
        video_id = extraer_video_id(url)
    except UrlNoAdmitida as e:
        raise ErrorLocal("URL_NO_ADMITIDA", str(e)) from None
    objetivo = (objetivo or "").strip() or None
    if objetivo is not None and len(objetivo) > MAX_OBJETIVO:
        raise ErrorLocal(
            "OBJETIVO_LARGO", f"El objetivo supera los {MAX_OBJETIVO} caracteres."
        )
    if idioma is not None:
        try:
            idioma = validar_idioma(idioma)
        except ErrorConfig as err:
            raise ErrorLocal("IDIOMA_INVALIDO", str(err)) from None
    modo = "transcripcion" if solo_transcripcion else "analisis"

    with ctx.cola.candado():
        proyectos = preparar_proyectos(ctx)
        proy = None
        sufijo = ""
        creado_proy = False
        if proyecto:
            proy, creado = proyectos.resolver_o_crear(proyecto)
            creado_proy = creado
            if creado:
                sufijo = f" Proyecto «{proy.nombre}» creado."
            if objetivo is None and modo == "analisis":
                objetivo = proy.objetivo
        if objetivo is not None and len(objetivo) > MAX_OBJETIVO:
            raise ErrorLocal(
                "OBJETIVO_LARGO", f"El objetivo supera los {MAX_OBJETIVO} caracteres."
            )
        normalizado = normalizar_objetivo(objetivo)

        if not forzar:
            existente = (
                ctx.biblioteca.tiene_transcripcion(video_id)
                if solo_transcripcion
                else ctx.biblioteca.buscar_analisis(video_id, objetivo)
            )
            if existente is not None:
                if proy is not None:
                    proyectos.agregar_video(proy.id, video_id)
                    actualizar_vistas(ctx)
                return ResultadoEncolar(
                    True,
                    _aviso_duplicado(f"Ya está en la biblioteca: {existente}", url, canal) + sufijo,
                    None,
                    estimacion(None),
                )
        # Desde el MCP, `forzar` solo salta la biblioteca: duplicar un trabajo activo no aporta nada.
        if not forzar or canal == "mcp":
            activo = ctx.cola.buscar_activo(video_id, normalizado, modo)
            if activo is not None:
                if proy is not None:
                    proyectos.agregar_video(proy.id, video_id)
                    if not activo.get("proyecto"):
                        ctx.cola.actualizar_si(
                            activo["id_local"], {"proyecto": activo.get("proyecto")}, {"proyecto": proy.id}
                        )
                    actualizar_vistas(ctx)
                return ResultadoEncolar(
                    True,
                    _aviso_duplicado(
                        f"Ya está en la cola con el id {activo['id_local']}", url, canal, en_cola=True
                    ) + sufijo,
                    activo,
                    estimacion(activo.get("duracion_s")),
                )

        if creado_proy:
            actualizar_vistas(ctx)
        _verificar_claves(ctx.cfg, modo, canal)
        if proy is not None:
            proyectos.agregar_video(proy.id, video_id)
        trabajo = ctx.cola.crear(
            {
                "url": url,
                "video_id": video_id,
                "modo": modo,
                "objetivo": objetivo,
                "idioma": idioma or ctx.cfg.idioma,
                "objetivo_normalizado": normalizado,
                "proyecto": proy.id if proy is not None else None,
                "testigo_estado": "desactivado" if sin_testigo else "pendiente",
            }
        )
        if proy is not None:
            actualizar_vistas(ctx)
        aviso = (
            _aviso_analisis_previos(ctx, video_id, normalizado, canal) if modo == "analisis" else None
        )
        mensaje = f"Trabajo {trabajo['id_local']} registrado." + sufijo
        return ResultadoEncolar(
            False, f"{mensaje} {aviso}" if aviso else mensaje, trabajo, estimacion(None), aviso
        )


# --- enviar ---


def _limpio(ctx: Contexto, texto: str) -> str:
    cfg = ctx.cfg
    for clave in (cfg.clave_yti, cfg.clave_gemini, cfg.clave_anthropic):
        if not _falta(clave):
            texto = texto.replace(clave, "***")
    return texto


def _cambiar(ctx: Contexto, trabajo: dict, cambios: dict, *, liberar: bool) -> None:
    """Aplica `cambios` solo si este proceso sigue siendo quien tiene la toma."""
    if liberar:
        cambios = {**cambios, "tomado_por": None, "tomado_en": None}
    actualizado = ctx.cola.actualizar_si(
        trabajo["id_local"],
        {"estado": "enviando", "tomado_por": ctx.cola.id_proceso},
        cambios,
    )
    if actualizado is None:
        raise _TomaPerdida
    trabajo.clear()
    trabajo.update(actualizado)


def _error_final(ctx: Contexto, e: ErrorApi) -> dict:
    codigo = e.codigo
    mensaje = _limpio(ctx, e.mensaje)
    if isinstance(e, ClaveYtiInvalida):
        mensaje = "La clave de yt-insights es inválida o fue revocada; revisa `yti config`."
    elif codigo == "FALTA_CLAVE_GEMINI":
        mensaje = "Falta la clave de Gemini; configúrala con `yti config --clave gemini`."
    elif codigo == "FALTA_CLAVE_ANTHROPIC":
        mensaje = mensaje_falta_anthropic()
    elif codigo == "CLAVE_MAL_FORMADA":
        mensaje = "Una clave tiene un formato inválido; revisa `yti config`."
    elif codigo in ("FUENTE_NO_ADMITIDA", "CALLBACK_NO_PERMITIDO"):
        mensaje = f"{mensaje} ({_REPORTAR})"
    return {"codigo": codigo, "mensaje": mensaje}


def _medianoche_siguiente(ahora: datetime) -> datetime:
    manana = ahora.astimezone(timezone.utc).date() + timedelta(days=1)
    return datetime.combine(manana, time(0, 0), tzinfo=timezone.utc)


def _testigo_a_enviar(ctx: Contexto, trabajo: dict) -> dict | None:
    if trabajo.get("testigo_estado") != "obtenido":
        return None
    contenido = ctx.cola.leer_testigo(trabajo["id_local"])
    if contenido is None:
        return None
    return {"formato": "vtt", "idioma": trabajo.get("testigo_idioma") or "und", "contenido": contenido}


def _obtener_testigo(ctx: Contexto, trabajo: dict) -> bool:
    """Devuelve False si no hay conexión (el trabajo queda `por_enviar`)."""
    try:
        res = ctx.obtener_testigo(trabajo["url"])
    except SinConexion:
        _cambiar(
            ctx, trabajo,
            {"estado": "por_enviar", "motivo_sin_envio": "sin conexión para obtener el testigo"},
            liberar=True,
        )
        return False
    info = res.info
    cambios = {
        "testigo_estado": res.estado,
        "motivo_sin_testigo": res.motivo,
        "titulo_local": info.titulo if info else None,
        "canal_local": info.canal if info else None,
        "duracion_s": info.duracion_s if info else None,
    }
    if res.estado == "obtenido" and res.contenido is not None:
        ctx.cola.guardar_testigo(trabajo["id_local"], res.contenido)
        cambios["testigo_idioma"] = res.idioma
    _cambiar(ctx, trabajo, cambios, liberar=False)
    return True


def _post(ctx: Contexto, api: ClienteApi, trabajo: dict) -> str:
    """POST con un único reenvío sin testigo si el servidor lo rechaza (R13)."""
    cfg = ctx.cfg
    testigo = _testigo_a_enviar(ctx, trabajo)
    while True:
        try:
            return api.crear_trabajo(
                url=trabajo["url"],
                modo=trabajo["modo"],
                objetivo=trabajo.get("objetivo"),
                idioma=trabajo.get("idioma") or cfg.idioma,
                testigo=testigo,
                clave_gemini=cfg.clave_gemini,
                clave_anthropic=cfg.clave_anthropic,
            )
        except TestigoInvalido:
            if testigo is None:
                raise
            testigo = None
            ctx.cola.borrar_testigo(trabajo["id_local"])
            _cambiar(
                ctx, trabajo,
                {
                    "testigo_estado": "sin_subtitulos",
                    "motivo_sin_testigo": "rechazado por el servidor (TESTIGO_INVALIDO)",
                },
                liberar=False,
            )


def _enviar(ctx: Contexto, trabajo: dict) -> dict:
    cfg = ctx.cfg
    try:
        _verificar_claves(cfg, trabajo["modo"])
    except ErrorLocal as e:
        _cambiar(ctx, trabajo, {"estado": "por_enviar", "motivo_sin_envio": e.mensaje}, liberar=True)
        raise
    if trabajo.get("testigo_estado") == "pendiente" and not _obtener_testigo(ctx, trabajo):
        return trabajo
    with ctx.api() as api:
        try:
            api.despertar()
            if not contrato_compatible(api, ctx.ruta_cache_version, ctx.reloj()):
                _cambiar(
                    ctx, trabajo,
                    {"estado": "por_enviar", "motivo_sin_envio": _AVISO_VERSION},
                    liberar=True,
                )
                return trabajo
            # Renueva la toma justo antes del POST: un lote lento no deja que otro proceso la tome (§9.3).
            _cambiar(ctx, trabajo, {"tomado_en": iso(ctx.reloj())}, liberar=False)
            id_remoto = _post(ctx, api, trabajo)
        except TopeDiario as e:
            ahora = ctx.reloj()
            hasta = (
                ahora + timedelta(seconds=e.retry_after_s)
                if e.retry_after_s is not None
                else _medianoche_siguiente(ahora)
            )
            _cambiar(
                ctx, trabajo,
                {"estado": "retenido", "enviar_despues": iso(hasta), "motivo_sin_envio": None},
                liberar=True,
            )
        except RespuestaPerdida:
            _cambiar(
                ctx, trabajo,
                {"estado": "por_enviar", "motivo_sin_envio": "respuesta perdida"},
                liberar=True,
            )
        except ServicioNoDisponible:
            _cambiar(
                ctx, trabajo,
                {"estado": "por_enviar", "motivo_sin_envio": "servicio no disponible"},
                liberar=True,
            )
        except ErrorApi as e:
            error = _error_final(ctx, e)
            ctx.cola.borrar_testigo(trabajo["id_local"])
            _cambiar(
                ctx, trabajo, {"estado": "error", "error": error, "motivo_sin_envio": None},
                liberar=True,
            )
        else:
            _cambiar(
                ctx, trabajo,
                {
                    "estado": "enviado", "id_remoto": id_remoto, "estado_remoto": "pendiente",
                    "intentos_remotos": 0, "enviar_despues": None, "motivo_sin_envio": None,
                    "error": None,
                },
                liberar=True,
            )
    return trabajo


def enviar(ctx: Contexto, trabajo: dict) -> dict:
    """Pasos 5 y 6 de §9.2. Devuelve el trabajo actualizado, liberado de la toma."""
    try:
        return _enviar(ctx, trabajo)
    except _TomaPerdida:
        log.warning("El trabajo %s fue tomado por otro proceso; se abandona.", trabajo["id_local"])
        return trabajo


def _mensaje_resultado(trabajo: dict) -> str:
    estado = trabajo.get("estado")
    id_local = trabajo["id_local"]
    if estado == "enviado":
        return f"Trabajo {id_local} enviado."
    if estado == "retenido":
        local = desde_iso(trabajo["enviar_despues"]).astimezone().strftime("%Y-%m-%d %H:%M")
        return f"Trabajo {id_local}: tope diario alcanzado; se enviará a partir de {local}."
    if estado == "error":
        error = trabajo.get("error") or {}
        return f"Trabajo {id_local} con error ({error.get('codigo')}): {error.get('mensaje')}"
    motivo = trabajo.get("motivo_sin_envio") or "servicio no disponible"
    return f"Trabajo {id_local} guardado ({motivo}); se enviará en la próxima activación."


def encolar(ctx: Contexto, url: str, **kw) -> ResultadoEncolar:
    """`registrar` y, si no es duplicado, `enviar` (flujo de la terminal)."""
    res = registrar(ctx, url, **kw)
    if res.duplicado:
        return res
    trabajo = enviar(ctx, res.trabajo)
    mensaje = _mensaje_resultado(trabajo)
    return ResultadoEncolar(
        False,
        f"{mensaje}\n{res.aviso}" if res.aviso else mensaje,
        trabajo,
        estimacion(trabajo.get("duracion_s")),
        res.aviso,
    )


# --- activar: sondeo, baja y espera (§9.3, §10.2) ---

ESTADOS_FINALES = ("en_biblioteca", "error", "perdido")
_PAUSA_INICIAL_S = 15
_PAUSA_LARGA_S = 60
_FASE_INICIAL_S = 120


@dataclass
class ResumenActivacion:
    enviados: list[dict]
    bajados: list[dict]
    errores: list[dict]
    avisos: list[str]


def hay_activos(ctx: Contexto) -> bool:
    return ctx.cola.hay_activos()


def _aplicar(ctx: Contexto, trabajo: dict, cambios: dict) -> bool:
    """Aplica `cambios` solo si el trabajo sigue `enviado` con el mismo `id_remoto`."""
    actualizado = ctx.cola.actualizar_si(
        trabajo["id_local"], {"id_remoto": trabajo["id_remoto"], "estado": "enviado"}, cambios
    )
    if actualizado is None:
        log.warning("El trabajo %s cambió durante el sondeo; se descarta la respuesta.", trabajo["id_local"])
        return False
    trabajo.clear()
    trabajo.update(actualizado)
    return True


def _poner_error(ctx: Contexto, trabajo: dict, codigo: str, mensaje: str) -> bool:
    if not _aplicar(ctx, trabajo, {"estado": "error", "error": {"codigo": codigo, "mensaje": mensaje}}):
        return False
    ctx.cola.borrar_testigo(trabajo["id_local"])
    return True


def _mensaje_error_remoto(ctx: Contexto, codigo: str | None, mensaje: str | None) -> str:
    if codigo == "GEMINI_AUTH":
        return "La clave de Gemini fue rechazada; revísala con `yti config --clave gemini`."
    if codigo in ("CLAUDE_AUTH", "SIN_API_KEY_CLAUDE"):
        return (
            "La clave de Anthropic es inválida, falta o no tiene saldo; "
            "revísala con `yti config --clave anthropic`."
        )
    return _limpio(ctx, mensaje or "El servicio no informó el motivo.")


def _error_remoto(ctx: Contexto, trabajo: dict, error: dict) -> None:
    codigo = error.get("codigo") or "ERROR_REMOTO"
    if codigo == "CLAVES_NO_DISPONIBLES":
        if not trabajo.get("reenvios_claves"):
            # el testigo guardado y su idioma se conservan para el reenvío
            _aplicar(
                ctx, trabajo,
                {"reenvios_claves": 1, "estado": "por_enviar", "id_remoto": None,
                 "estado_remoto": None, "motivo_sin_envio": "el servidor perdió las claves; se reenvía"},
            )
            return
        mensaje = (
            "El servidor no conservó las claves tras un reenvío; "
            "vuelve a encolar el video a mano."
        )
    else:
        mensaje = _mensaje_error_remoto(ctx, codigo, error.get("mensaje"))
    _poner_error(ctx, trabajo, codigo, mensaje)


def _enlazar_en_baja(ctx: Contexto, ref: str, video_id: str) -> None:
    """Un id no toca la pertenencia (ya la enlazó `registrar`); un nombre legado se resuelve o crea."""
    try:
        proyectos = preparar_proyectos(ctx)
        if es_id_proyecto(ref):
            proyectos.resolver(ref)
        else:
            p, _ = proyectos.resolver_o_crear(ref)
            proyectos.agregar_video(p.id, video_id)
    except Exception as e:  # noqa: BLE001 - enlazar nunca impide dejar el trabajo en la biblioteca
        log.warning("la baja no pudo enlazar el video %s al proyecto %s: %s",
                    video_id, ref, getattr(e, "mensaje", e))


def bajar_resultado(ctx: Contexto, trabajo: dict, vista: dict) -> dict:
    """Escribe el resultado en la biblioteca y deja el trabajo `en_biblioteca` (idempotente)."""
    id_local = trabajo["id_local"]
    with ctx.cola.candado():
        try:
            actual = ctx.cola.leer(id_local)
        except (OSError, ValueError):
            return trabajo
        if actual.get("id_remoto") != trabajo.get("id_remoto") or actual.get("estado") not in (
            "enviado", "en_biblioteca"
        ):
            log.warning("El trabajo %s cambió; no se baja el resultado.", id_local)
            return actual
        resultado = vista.get("resultado") or {}
        cuerpo = resultado.get("transcripcion_md")
        if not cuerpo:
            _poner_error(
                ctx, trabajo, "CONTRATO_INCOMPATIBLE",
                "El resultado no incluye la transcripción; " + _AVISO_VERSION,
            )
            return trabajo
        video_id = vista.get("video_id") or trabajo["video_id"]
        meta = resultado.get("metadatos") or {}
        titulo = meta.get("titulo") or trabajo.get("titulo_local") or None
        canal = meta.get("canal") or trabajo.get("canal_local") or "desconocido"
        segundos = trabajo.get("duracion_s")
        if segundos is None:
            segundos = meta.get("duracion_s")
        duracion = marca_tiempo(segundos) if segundos is not None else "desconocida"
        verif = resultado.get("verificacion") or {}
        estado_verif, pct = verif.get("estado"), verif.get("coincidencia_pct")
        uso = vista.get("uso")
        costo = ((uso or {}).get("costo_usd") or {}).get("total")
        obtenido = iso(ctx.reloj())
        id_remoto = trabajo["id_remoto"]
        bib = ctx.biblioteca
        bib.escribir_transcripcion(
            video_id, titulo,
            {
                "video_id": video_id, "titulo": titulo or video_id, "canal": canal,
                "url": trabajo.get("url"), "duracion": duracion,
                "fuente": vista.get("metodo_transcripcion"),
                "fuente_testigo": vista.get("fuente_testigo"),
                "verificacion": estado_verif, "coincidencia_pct": pct,
                "obtenido": obtenido, "trabajo": id_remoto,
            },
            cuerpo,
        )
        analisis = resultado.get("analisis")
        aviso = None
        if trabajo.get("modo") == "analisis" and not isinstance(analisis, dict):
            aviso = "el servidor no devolvió el análisis; solo se guardó la transcripción"
        if trabajo.get("modo") == "analisis" and isinstance(analisis, dict):
            bib.escribir_analisis(
                video_id, titulo, trabajo.get("objetivo"),
                {
                    "video_id": video_id, "objetivo": trabajo.get("objetivo"),
                    "modelo": vista.get("modelo"), "obtenido": obtenido, "costo_usd": costo,
                    "verificacion": estado_verif, "coincidencia_pct": pct, "trabajo": id_remoto,
                },
                analisis_a_markdown(
                    analisis, titulo=titulo or video_id, video_id=video_id,
                    objetivo=trabajo.get("objetivo"),
                    verificacion_texto=texto_verificacion(estado_verif, pct),
                ),
            )
        if trabajo.get("proyecto"):
            _enlazar_en_baja(ctx, trabajo["proyecto"], video_id)
        bib.regenerar_indice()
        actualizar_vistas(ctx)
        ctx.cola.borrar_testigo(id_local)
        cambios = {"estado": "en_biblioteca", "estado_remoto": "completado", "uso": uso, "error": None}
        if aviso:
            cambios["aviso"] = aviso
        actualizado = ctx.cola.actualizar_si(id_local, {"id_remoto": id_remoto}, cambios)
        if actualizado is not None:
            trabajo.clear()
            trabajo.update(actualizado)
        return trabajo


def _sondear(ctx: Contexto, api: ClienteApi, trabajo: dict, resumen: ResumenActivacion) -> None:
    id_local = trabajo["id_local"]
    try:
        vista = api.consultar(trabajo["id_remoto"])
    except ClaveYtiInvalida:
        resumen.avisos.append(f"{id_local}: la clave de yt-insights fue rechazada; revisa `yti config`.")
        return
    except TrabajoNoEncontrado:
        if _aplicar(ctx, trabajo, {"estado": "perdido"}):
            ctx.cola.borrar_testigo(id_local)
            resumen.avisos.append(f"{id_local}: el servidor ya no conoce el trabajo; se marca perdido.")
        return
    except ServicioNoDisponible:
        return
    except ContratoIncompatible:
        # §10.3: falta `estado`; se marca error y se conserva el id_remoto
        _poner_error(
            ctx, trabajo, "CONTRATO_INCOMPATIBLE",
            "La vista del trabajo no incluye el campo estado; " + _AVISO_VERSION,
        )
        if trabajo.get("estado") == "error":
            resumen.errores.append({"id_local": id_local, **(trabajo.get("error") or {})})
        return
    except ErrorApi as e:
        resumen.avisos.append(f"{id_local}: {_limpio(ctx, e.mensaje)}")
        return
    estado = vista.get("estado")
    if estado == "completado":
        bajar_resultado(ctx, trabajo, vista)
        if trabajo.get("estado") == "en_biblioteca" and trabajo.get("aviso"):
            resumen.avisos.append(f"{id_local}: {trabajo['aviso']}")
    elif estado == "error":
        _error_remoto(ctx, trabajo, vista.get("error") or {})
    else:
        _aplicar(ctx, trabajo, {"estado_remoto": estado, "intentos_remotos": vista.get("intentos", 0)})
    if trabajo.get("estado") == "en_biblioteca":
        resumen.bajados.append(trabajo)
    elif trabajo.get("estado") == "error":
        resumen.errores.append({"id_local": id_local, **(trabajo.get("error") or {})})


def _liberar_si_sin_remoto(ctx: Contexto, trabajo: dict) -> None:
    """Devuelve a `por_enviar` un trabajo que falló sin `id_remoto`; si ya lo tiene, no se toca."""
    try:
        guardado = ctx.cola.leer(trabajo["id_local"])
        if trabajo.get("id_remoto") or guardado.get("id_remoto"):
            return
        _cambiar(ctx, trabajo, {"estado": "por_enviar"}, liberar=True)
    except Exception:  # toma perdida u otro fallo: la toma vence sola a los 10 min
        log.exception("No se pudo liberar el trabajo %s", trabajo["id_local"])


def activar(ctx: Contexto) -> ResumenActivacion:
    """Pasos 1 a 3 de §9.3: envía lo pendiente, sondea lo enviado y baja lo completado."""
    resumen = ResumenActivacion([], [], [], [])
    for trabajo in ctx.cola.tomar_para_envio():
        try:
            enviar(ctx, trabajo)
        except ErrorLocal as e:
            resumen.errores.append({"id_local": trabajo["id_local"], "codigo": e.codigo, "mensaje": e.mensaje})
            continue
        except Exception as e:  # un trabajo no aborta la activación
            log.exception("Falló el envío del trabajo %s", trabajo["id_local"])
            _liberar_si_sin_remoto(ctx, trabajo)
            resumen.errores.append(
                {"id_local": trabajo["id_local"], "codigo": "ERROR_LOCAL", "mensaje": _limpio(ctx, str(e))}
            )
            continue
        estado = trabajo.get("estado")
        if estado == "enviado":
            resumen.enviados.append(trabajo)
        elif estado == "error":
            resumen.errores.append({"id_local": trabajo["id_local"], **(trabajo.get("error") or {})})
        elif trabajo.get("motivo_sin_envio"):
            resumen.avisos.append(f"{trabajo['id_local']}: {trabajo['motivo_sin_envio']}")
    enviados = [t for t in ctx.cola.listar() if t.get("estado") == "enviado" and t.get("id_remoto")]
    if enviados:
        try:
            with ctx.api() as api:
                for trabajo in enviados:
                    try:
                        _sondear(ctx, api, trabajo, resumen)
                    except Exception as e:
                        log.exception("Falló el sondeo del trabajo %s", trabajo["id_local"])
                        resumen.errores.append(
                            {"id_local": trabajo["id_local"], "codigo": "ERROR_LOCAL",
                             "mensaje": _limpio(ctx, str(e))}
                        )
        except ErrorLocal as e:
            resumen.errores.append({"id_local": None, "codigo": e.codigo, "mensaje": e.mensaje})
    return resumen


def esperar(
    ctx: Contexto,
    id_local: str | None = None,
    *,
    tope_min: int = 30,
    dormir: Callable[[float], None] = _time.sleep,
) -> list[dict]:
    """Activa y duerme (15 s los primeros 120 s, luego 60 s) hasta que todo termine o venza el tope."""
    if id_local is not None:
        ids = [id_local]
    else:
        ids = [t["id_local"] for t in ctx.cola.listar() if t.get("estado") not in ESTADOS_FINALES]
    inicio = ctx.reloj()
    while True:
        activar(ctx)
        observados = []
        for i in ids:
            try:
                observados.append(ctx.cola.leer(i))
            except (OSError, ValueError):
                raise ErrorLocal("TRABAJO_NO_ENCONTRADO", f"No existe el trabajo local {i}.") from None
        transcurrido = (ctx.reloj() - inicio).total_seconds()
        if all(t.get("estado") in ESTADOS_FINALES for t in observados) or transcurrido >= tope_min * 60:
            return observados
        dormir(_PAUSA_INICIAL_S if transcurrido < _FASE_INICIAL_S else _PAUSA_LARGA_S)
