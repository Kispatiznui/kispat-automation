"""Errores tipados. Cada clase mapea a un código de salida (ver runner)."""


class DataflowError(Exception):
    exit_code = 1

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


class ConfigError(DataflowError):
    """Pipeline inválido. Definitivo: reintentar no ayuda."""
    exit_code = 4


class DataError(DataflowError):
    """Entrada ilegible o inválida. Definitivo salvo que cambien las entradas."""
    exit_code = 3


class InternalError(DataflowError):
    """Invariante rota o fallo inesperado. Posiblemente recuperable."""
    exit_code = 1
