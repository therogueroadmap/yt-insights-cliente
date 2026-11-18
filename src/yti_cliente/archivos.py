import os
import tempfile
from pathlib import Path


def escribir_atomico(ruta: Path, texto: str) -> None:
    """Escribe `texto` en `ruta` de forma atómica (temporal en el mismo directorio + os.replace)."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, temporal = tempfile.mkstemp(dir=ruta.parent, prefix=f".{ruta.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(texto)
        os.replace(temporal, ruta)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
