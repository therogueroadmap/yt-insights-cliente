"""Terminal `yti` (§8.2, §8.3)."""

import argparse
import getpass
import importlib
import sys
from pathlib import Path
from typing import Sequence

from yti_cliente.cola import EsquemaMasNuevo, desde_iso
from yti_cliente.config import (
    ErrorConfig,
    cargar_config,
    claves_cargadas,
    enmascarar,
    guardar_valor,
    ruta_config,
    validar_clave_proveedor,
    validar_idioma,
)
from yti_cliente.ejecutor import (
    ErrorLocal,
    activar,
    crear_contexto,
    con_proyectos,
    encolar,
    esperar,
    estimacion,
)
from yti_cliente.proyectos import SIN_CAMBIO
from yti_cliente.proyectos_vista import texto_vista
from yti_cliente.registro import configurar_registro, redactar
from yti_cliente.youtube import UrlNoAdmitida, resolver_ref_video

_CLAVES_CLI = {"yt-insights": "yt_insights", "gemini": "gemini", "anthropic": "anthropic"}
_ORIGENES = {"entorno": "entorno", "archivo": "archivo", "defecto": "por defecto", "ausente": "sin configurar"}
_COLUMNAS = ("id local", "video", "objetivo", "estado", "remoto", "intentos", "nota")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yti", description="Cliente local de yt-insights.")
    sub = p.add_subparsers(dest="comando", required=True, metavar="comando")

    c = sub.add_parser("config", help="muestra o cambia la configuración")
    c.add_argument("--clave", choices=sorted(_CLAVES_CLI), help="pide la clave sin eco y la guarda")
    c.add_argument("--biblioteca", help="carpeta de la biblioteca")
    c.add_argument("--api-url", dest="api_url", help="URL de la API")
    c.add_argument("--idioma", help="idioma de salida del análisis (código ISO 639-1, p. ej. es, en, pt-BR)")

    e = sub.add_parser("encolar", help="encola un video de YouTube")
    e.add_argument("url")
    e.add_argument("--objetivo")
    e.add_argument("--solo-transcripcion", action="store_true")
    e.add_argument("--sin-testigo", action="store_true")
    e.add_argument("--proyecto")
    e.add_argument("--forzar", action="store_true")

    sub.add_parser("estado", help="activa la cola y muestra su estado")

    w = sub.add_parser("esperar", help="espera hasta que los trabajos terminen")
    w.add_argument("id_local", nargs="?")
    w.add_argument("--tope-min", type=int, default=30, dest="tope_min")

    li = sub.add_parser("lista", help="lista los videos de la biblioteca")
    li.add_argument("--buscar")

    pr = sub.add_parser("proyecto", help="administra los proyectos locales")
    acc = pr.add_subparsers(dest="accion", required=True, metavar="acción")
    a = acc.add_parser("crear", help="crea un proyecto")
    a.add_argument("nombre")
    a.add_argument("--objetivo", help="objetivo por defecto de los videos del proyecto")
    acc.add_parser("lista", help="lista los proyectos")
    a = acc.add_parser("ver", help="muestra el proyecto")
    a.add_argument("nombre")
    a = acc.add_parser("agregar", help="agrega videos al proyecto (lo crea si no existe) sin encolarlos")
    a.add_argument("nombre")
    a.add_argument("videos", nargs="+", metavar="video", help="URL o id de video de YouTube")
    a = acc.add_parser("quitar", help="quita un video del proyecto")
    a.add_argument("nombre")
    a.add_argument("video", help="URL o id de video de YouTube")
    a = acc.add_parser("editar", help="cambia el nombre o el objetivo del proyecto")
    a.add_argument("nombre")
    a.add_argument("--nombre", dest="nuevo_nombre", help="nombre nuevo")
    a.add_argument("--objetivo", help="objetivo nuevo; con una cadena vacía se borra")
    a = acc.add_parser("nota", help="agrega una nota al proyecto")
    a.add_argument("nombre")
    a.add_argument("texto")
    a = acc.add_parser("borrar", help="borra el proyecto (no toca los videos)")
    a.add_argument("nombre")

    sub.add_parser("mcp", help="arranca el servidor MCP por stdio")
    return p


def _config(args) -> int:
    if args.clave is None and args.biblioteca is None and args.api_url is None and args.idioma is None:
        cfg = cargar_config()
        print(f"Archivo: {ruta_config()}")
        print(f"api_url: {cfg.api_url} ({_ORIGENES[cfg.origen['api_url']]})")
        print(f"biblioteca: {cfg.biblioteca} ({_ORIGENES[cfg.origen['biblioteca']]})")
        print(f"idioma: {cfg.idioma} ({_ORIGENES[cfg.origen['idioma']]})")
        for campo, valor in (
            ("yt_insights", cfg.clave_yti), ("gemini", cfg.clave_gemini), ("anthropic", cfg.clave_anthropic),
        ):
            print(f"{campo}: {enmascarar(valor)} ({_ORIGENES[cfg.origen[campo]]})")
        return 0
    if args.clave is not None:
        valor = getpass.getpass(f"Clave de {args.clave} (no se muestra): ").strip()
        if not valor:
            raise ErrorConfig("La clave está vacía; no se guardó nada.")
        if args.clave != "yt-insights" and not validar_clave_proveedor(valor):
            raise ErrorConfig(
                f"La clave de {args.clave} tiene un formato inválido "
                "(20 a 512 caracteres: letras, números, `.`, `_` y `-`); no se guardó nada."
            )
        guardar_valor(_CLAVES_CLI[args.clave], valor)
        print(f"Clave de {args.clave} guardada.")
    if args.biblioteca is not None:
        ruta_biblioteca = str(Path(args.biblioteca).expanduser().resolve())
        guardar_valor("biblioteca", ruta_biblioteca)
        print(f"Biblioteca: {ruta_biblioteca}")
    if args.api_url is not None:
        guardar_valor("api_url", args.api_url)
        print(f"api_url: {args.api_url}")
    if args.idioma is not None:
        idioma = validar_idioma(args.idioma)
        guardar_valor("idioma", idioma)
        print(f"idioma: {idioma}")
    return 0


def _nota(t: dict) -> str:
    if t.get("estado") == "retenido" and t.get("enviar_despues"):
        local = desde_iso(t["enviar_despues"]).astimezone().strftime("%Y-%m-%d %H:%M")
        return f"se enviará a partir de {local}"
    if t.get("error"):
        e = t.get("error") or {}
        return f"{e.get('codigo')}: {e.get('mensaje')}"
    if t.get("estado") in ("por_enviar", "enviando", "enviado"):
        return estimacion(t.get("duracion_s"))
    return ""


def _tabla(trabajos: list[dict]) -> str:
    if not trabajos:
        return "La cola está vacía."
    filas = [_COLUMNAS] + [
        (
            t["id_local"], t.get("video_id") or "-", t.get("objetivo") or "-",
            t.get("estado") or "-", t.get("estado_remoto") or "-",
            str(t.get("intentos_remotos") or 0), _nota(t),
        )
        for t in trabajos
    ]
    filas = [tuple(" ".join(str(c).split()) for c in f) for f in filas]
    anchos = [max(len(f[i]) for f in filas) for i in range(len(_COLUMNAS))]
    return "\n".join("  ".join(c.ljust(a) for c, a in zip(f, anchos)).rstrip() for f in filas)


def _encolar(ctx, args) -> int:
    res = encolar(
        ctx, args.url, objetivo=args.objetivo, solo_transcripcion=args.solo_transcripcion,
        sin_testigo=args.sin_testigo, proyecto=args.proyecto, forzar=args.forzar,
    )
    if res.duplicado:
        print(res.mensaje)
        return 0
    t = res.trabajo
    print(f"Id local: {t['id_local']}")
    print(f"Id remoto: {t.get('id_remoto') or '-'}")
    print(f"Estado: {t.get('estado')}")
    print(f"Estimación: {res.estimacion}")
    print(res.mensaje)
    return 0


def _estado(ctx) -> int:
    resumen = activar(ctx)
    for aviso in resumen.avisos:
        print(f"Aviso: {aviso}")
    for e in resumen.errores:
        if e.get("id_local") is None:
            print(f"Error: {e.get('codigo')}: {e.get('mensaje')}", file=sys.stderr)
    print(_tabla(ctx.cola.listar()))
    return 0


def _esperar(ctx, args) -> int:
    try:
        trabajos = esperar(ctx, args.id_local, tope_min=args.tope_min)
    except KeyboardInterrupt:
        print("Interrumpido; los trabajos siguen en la cola (consulta `yti estado`).", file=sys.stderr)
        return 130
    print(_tabla(trabajos))
    return 0


def _lista(ctx, args) -> int:
    print(ctx.biblioteca.formatear_lista(ctx.biblioteca.listar(args.buscar)))
    return 0


def _ref_video(texto: str) -> str:
    try:
        return resolver_ref_video(texto)
    except UrlNoAdmitida as e:
        raise ErrorLocal("URL_NO_ADMITIDA", str(e)) from None


def _tabla_proyectos(proyectos) -> str:
    filas = [("nombre", "objetivo", "videos")]
    filas += [(p.nombre, p.objetivo or "-", str(len(p.videos))) for p in proyectos]
    anchos = [max(len(f[i]) for f in filas) for i in range(3)]
    return "\n".join("  ".join(c.ljust(a) for c, a in zip(f, anchos)).rstrip() for f in filas)


def _proyecto(ctx, args) -> int:
    accion = args.accion
    if accion == "agregar":
        ids = [_ref_video(v) for v in args.videos]  # valida todo antes de tocar nada
    elif accion == "quitar":
        id_video = _ref_video(args.video)

    def correr(ps) -> str | None:
        if accion == "crear":
            p = ps.crear(args.nombre, args.objetivo)
            return f"Proyecto «{p.nombre}» creado (id {p.id})."
        if accion == "lista":
            lista = ps.listar()
            return _tabla_proyectos(lista) if lista else "No hay proyectos."
        if accion == "ver":
            return texto_vista(ps, ps.resolver(args.nombre))
        if accion == "agregar":
            p, creado = ps.resolver_o_crear(args.nombre)
            nuevos = sum(ps.agregar_video(p.id, v) for v in ids)
            partes = [f"Proyecto «{p.nombre}» creado."] if creado else []
            partes.append(f"Videos agregados a «{p.nombre}»: {nuevos} de {len(ids)}.")
            return "\n".join(partes)
        if accion == "quitar":
            p = ps.quitar_video(args.nombre, id_video)
            return f"Video {id_video} quitado de «{p.nombre}»."
        if accion == "editar":
            objetivo = SIN_CAMBIO if args.objetivo is None else args.objetivo
            p = ps.editar(args.nombre, args.nuevo_nombre, objetivo)
            return f"Proyecto «{p.nombre}» actualizado."
        if accion == "nota":
            p = ps.anotar(args.nombre, args.texto, "cli")
            return f"Nota agregada a «{p.nombre}»."
        p = ps.borrar(args.nombre)
        return f"Proyecto «{p.nombre}» borrado."

    salida, aviso = con_proyectos(ctx, correr)
    print(salida)
    if aviso:
        print(aviso, file=sys.stderr)
    return 0


def _mcp() -> int:
    importlib.import_module("yti_cliente.mcp_server").ejecutar()
    return 0


def _ejecutar(args) -> int:
    if args.comando == "config":
        return _config(args)
    cfg = cargar_config()
    configurar_registro(claves_cargadas(cfg))
    if args.comando == "mcp":
        return _mcp()
    ctx = crear_contexto(cfg)
    if args.comando == "encolar":
        return _encolar(ctx, args)
    if args.comando == "estado":
        return _estado(ctx)
    if args.comando == "esperar":
        return _esperar(ctx, args)
    if args.comando == "proyecto":
        return _proyecto(ctx, args)
    return _lista(ctx, args)


def _claves_a_redactar() -> list[str]:
    try:
        return list(claves_cargadas(cargar_config()))
    except Exception:  # noqa: BLE001 - sin config legible no hay claves que redactar
        return []


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _ejecutar(args)
    except (ErrorLocal, ErrorConfig, EsquemaMasNuevo) as e:
        print(f"Error: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        print("Interrumpido.", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001 - la terminal no muestra trazas
        texto = redactar(" ".join(str(e).split()), _claves_a_redactar())
        print(f"Error inesperado ({type(e).__name__}): {texto}", file=sys.stderr)
    return 1
