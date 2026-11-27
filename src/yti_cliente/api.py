"""Cliente HTTP de la API de yt-insights: traduce respuestas a resultados o excepciones tipadas."""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from yti_cliente.archivos import escribir_atomico

log = logging.getLogger(__name__)

PLAZO_DESPERTAR_S = 90.0
VIGENCIA_CACHE = timedelta(hours=24)
_FORMATO_FECHA = "%Y-%m-%dT%H:%M:%SZ"
_RE_VERSION = re.compile(r"^0\.(\d+)\.(\d+)$")
_RE_ENTERO = re.compile(r"^\d+$")


class ErrorApi(Exception):
    def __init__(self, codigo: str, mensaje: str, http: int | None = None):
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo
        self.mensaje = mensaje
        self.http = http


class ClaveYtiInvalida(ErrorApi):
    pass


class RechazoCreacion(ErrorApi):
    pass


class TestigoInvalido(ErrorApi):
    __test__ = False  # evita que pytest lo recoja como clase de pruebas


class TopeDiario(ErrorApi):
    def __init__(
        self,
        codigo: str,
        mensaje: str,
        http: int | None = None,
        retry_after_s: int | None = None,
    ):
        super().__init__(codigo, mensaje, http)
        self.retry_after_s = retry_after_s


class ServicioNoDisponible(ErrorApi):
    pass


class RespuestaPerdida(ErrorApi):
    pass


class TrabajoNoEncontrado(ErrorApi):
    pass


class ContratoIncompatible(ErrorApi):
    pass


def parsear_retry_after(valor: str | None) -> int | None:
    """Entero de segundos ≥ 0; cualquier otra forma (fecha HTTP, negativo, texto) da None."""
    if valor is None:
        return None
    valor = valor.strip()
    if not _RE_ENTERO.match(valor):
        return None
    return int(valor)


def version_compatible(version: str) -> bool:
    """Acepta 0.y.z desde 0.2.2: los cambios de la API en 0.x son aditivos en /v1/jobs. 1.0 o mayor se rechaza."""
    m = _RE_VERSION.match(version.strip()) if isinstance(version, str) else None
    return m is not None and (int(m.group(1)), int(m.group(2))) >= (2, 2)


def _detalle(resp: httpx.Response):
    try:
        cuerpo = resp.json()
    except ValueError:
        return None
    return cuerpo.get("detail") if isinstance(cuerpo, dict) else None


def _codigo_y_mensaje(detalle, codigo_defecto: str, mensaje_defecto: str) -> tuple[str, str]:
    """Interpreta `detail` (§4.1): objeto, lista (validación) o cadena."""
    if isinstance(detalle, dict):
        codigo = detalle.get("codigo")
        mensaje = detalle.get("mensaje")
        return (
            codigo if isinstance(codigo, str) and codigo else codigo_defecto,
            mensaje if isinstance(mensaje, str) and mensaje else mensaje_defecto,
        )
    if isinstance(detalle, list):
        primero = detalle[0] if detalle else None
        msg = primero.get("msg") if isinstance(primero, dict) else None
        return "VALIDACION", msg if isinstance(msg, str) and msg else mensaje_defecto
    if isinstance(detalle, str) and detalle:
        return codigo_defecto, detalle
    return codigo_defecto, mensaje_defecto


def _sin_claves(texto: str, claves: tuple[str, ...]) -> str:
    for clave in claves:
        if clave and clave.strip():  # una clave en blanco convertiría los espacios del mensaje en ***
            texto = texto.replace(clave, "***")
    return texto


class ClienteApi:
    def __init__(
        self,
        api_url: str,
        clave_yti: str,
        *,
        transport: httpx.BaseTransport | None = None,
        plazo_s: float = 30.0,
    ):
        self._clave_yti = clave_yti
        self._http = httpx.Client(
            base_url=api_url, transport=transport, timeout=plazo_s
        )

    def __enter__(self) -> "ClienteApi":
        return self

    def __exit__(self, *exc) -> None:
        self.cerrar()

    def cerrar(self) -> None:
        self._http.close()

    def despertar(self) -> None:
        try:
            resp = self._http.get("/health/vivo", timeout=PLAZO_DESPERTAR_S)
        except httpx.HTTPError:
            raise ServicioNoDisponible(
                "SERVICIO_NO_DISPONIBLE", "No se pudo conectar con el servicio."
            ) from None
        if resp.status_code >= 400:
            raise ServicioNoDisponible(
                "SERVICIO_NO_DISPONIBLE",
                "El servicio no respondió correctamente.",
                resp.status_code,
            )

    def crear_trabajo(
        self,
        *,
        url: str,
        modo: str,
        objetivo: str | None,
        testigo: dict | None,
        clave_gemini: str,
        clave_anthropic: str | None,
        idioma: str,
    ) -> str:
        cabeceras = {"X-API-Key": self._clave_yti, "X-Gemini-Key": clave_gemini}
        if modo == "analisis" and clave_anthropic:
            cabeceras["X-Anthropic-Key"] = clave_anthropic
        cuerpo: dict = {"url": url, "modo": modo, "idioma": idioma}
        if objetivo is not None and objetivo.strip():
            cuerpo["objetivo"] = objetivo
        if testigo is not None:
            cuerpo["testigo"] = testigo
        claves = (self._clave_yti, clave_gemini, clave_anthropic or "")
        try:
            resp = self._http.post("/v1/jobs", json=cuerpo, headers=cabeceras)
        except (httpx.ReadTimeout, httpx.WriteTimeout):
            raise RespuestaPerdida(
                "RESPUESTA_PERDIDA",
                "No llegó la respuesta del servidor; el trabajo pudo haberse creado.",
            ) from None
        except httpx.HTTPError:
            raise ServicioNoDisponible(
                "SERVICIO_NO_DISPONIBLE", "No se pudo conectar con el servicio."
            ) from None
        if resp.status_code == 202:
            return self._id_de(resp)
        self._lanzar_creacion(resp, claves)

    @staticmethod
    def _id_de(resp: httpx.Response) -> str:
        try:
            cuerpo = resp.json()
        except ValueError:
            cuerpo = None
        id_remoto = cuerpo.get("id") if isinstance(cuerpo, dict) else None
        if not isinstance(id_remoto, str) or not id_remoto:
            raise ContratoIncompatible(
                "CONTRATO_INCOMPATIBLE",
                "La respuesta del servidor no incluye el id del trabajo.",
                resp.status_code,
            )
        return id_remoto

    @staticmethod
    def _lanzar_creacion(resp: httpx.Response, claves: tuple[str, ...]):
        estado = resp.status_code
        detalle = _detalle(resp)
        if estado == 401:
            raise ClaveYtiInvalida(
                "CLAVE_YTI_INVALIDA",
                "La clave de yt-insights (X-API-Key) fue rechazada.",
                estado,
            )
        if estado >= 500:
            codigo, mensaje = _codigo_y_mensaje(
                detalle, "SERVICIO_NO_DISPONIBLE", "El servicio no está disponible."
            )
            raise ServicioNoDisponible(codigo, _sin_claves(mensaje, claves), estado)
        if estado == 429:
            codigo, mensaje = _codigo_y_mensaje(
                detalle, "TOPE_DIARIO", "Se alcanzó el tope diario de trabajos."
            )
            raise TopeDiario(
                codigo,
                _sin_claves(mensaje, claves),
                estado,
                parsear_retry_after(resp.headers.get("Retry-After")),
            )
        codigo, mensaje = _codigo_y_mensaje(
            detalle, "RECHAZO_CREACION", "El servidor rechazó la petición."
        )
        mensaje = _sin_claves(mensaje, claves)
        if estado == 422 and codigo == "TESTIGO_INVALIDO":
            raise TestigoInvalido(codigo, mensaje, estado)
        raise RechazoCreacion(codigo, mensaje, estado)

    def consultar(self, id_remoto: str) -> dict:
        try:
            resp = self._http.get(
                f"/v1/jobs/{id_remoto}", headers={"X-API-Key": self._clave_yti}
            )
        except httpx.HTTPError:
            raise ServicioNoDisponible(
                "SERVICIO_NO_DISPONIBLE", "No se pudo conectar con el servicio."
            ) from None
        estado = resp.status_code
        if estado == 401:
            raise ClaveYtiInvalida(
                "CLAVE_YTI_INVALIDA",
                "La clave de yt-insights (X-API-Key) fue rechazada.",
                estado,
            )
        if estado == 404:
            raise TrabajoNoEncontrado(
                "TRABAJO_NO_ENCONTRADO", "El servidor no conoce ese trabajo.", estado
            )
        if estado >= 400:
            codigo, mensaje = _codigo_y_mensaje(
                _detalle(resp), "SERVICIO_NO_DISPONIBLE", "El servicio no respondió."
            )
            raise ServicioNoDisponible(codigo, mensaje, estado)
        try:
            vista = resp.json()
        except ValueError:
            vista = None
        if not isinstance(vista, dict) or "estado" not in vista:
            raise ContratoIncompatible(
                "CONTRATO_INCOMPATIBLE",
                "La vista del trabajo no incluye el campo estado.",
                estado,
            )
        return vista

    def consultar_version(self) -> tuple[str | None, bool]:
        """(versión, fallo_de_red). `fallo_de_red` es True si no hubo respuesta útil del servidor."""
        try:
            resp = self._http.get("/openapi.json")
            if resp.status_code >= 500:
                return None, True
            if resp.status_code != 200:
                return None, False
            cuerpo = resp.json()
        except httpx.HTTPError:
            return None, True
        except ValueError:
            return None, False
        info = cuerpo.get("info") if isinstance(cuerpo, dict) else None
        version = info.get("version") if isinstance(info, dict) else None
        return (version if isinstance(version, str) else None), False

    def version_api(self) -> str | None:
        return self.consultar_version()[0]


def _leer_cache(ruta: Path, ahora: datetime) -> tuple[bool, str | None]:
    """Devuelve (vigente, version); una caché ilegible o futura no es vigente."""
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        comprobado = datetime.strptime(datos["comprobado"], _FORMATO_FECHA).replace(
            tzinfo=timezone.utc
        )
        version = datos.get("version")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False, None
    if not (timedelta(0) <= ahora - comprobado < VIGENCIA_CACHE):
        return False, None
    return True, version if isinstance(version, str) else None


def contrato_compatible(cliente: ClienteApi, ruta_cache: Path, ahora: datetime) -> bool:
    """False solo si la versión conocida es incompatible; sin versión se asume compatible."""
    vigente, version = _leer_cache(ruta_cache, ahora)
    if not vigente:
        version, fallo_de_red = cliente.consultar_version()
        if version is None:
            log.warning(
                "No se pudo comprobar la versión de la API; se asume compatible."
            )
        if fallo_de_red:
            return True  # no se cachea un fallo transitorio: se reintenta en la próxima activación
        escribir_atomico(
            ruta_cache,
            json.dumps(
                {
                    "version": version,
                    "comprobado": ahora.astimezone(timezone.utc).strftime(
                        _FORMATO_FECHA
                    ),
                }
            ),
        )
    return True if version is None else version_compatible(version)
