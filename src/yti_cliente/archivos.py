import os
import tempfile
import time
from pathlib import Path

REINTENTOS = 10
ESPERA_S = 0.05


def reintentar_permiso(accion):
    """Ejecuta `accion()` reintentando ante PermissionError (Windows: otro lector tiene el archivo abierto)."""
    for intento in range(REINTENTOS):
        try:
            return accion()
        except PermissionError:
            if intento == REINTENTOS - 1:
                raise
            time.sleep(ESPERA_S)


def escribir_atomico(ruta: Path, texto: str) -> None:
    """Escribe `texto` en `ruta` de forma atómica (temporal en el mismo directorio + os.replace)."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, temporal = tempfile.mkstemp(dir=ruta.parent, prefix=f".{ruta.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(texto)
        reintentar_permiso(lambda: os.replace(temporal, ruta))
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
