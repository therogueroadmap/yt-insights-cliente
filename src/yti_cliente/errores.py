"""Errores locales del cliente, separados para evitar ciclos de importación."""


class ErrorLocal(Exception):
    def __init__(self, codigo: str, mensaje: str):
        super().__init__(f"{codigo}: {mensaje}")
        self.codigo = codigo
        self.mensaje = mensaje
