"""Errores del dominio.

`InvalidRequest` hereda de `ValueError` porque la capa HTTP actual traduce
`ValueError` a un 400, y los tests legacy dependen de ese comportamiento.
"""


class GatewayError(Exception):
    """Raíz de los errores propios del gateway."""


class InvalidRequest(GatewayError, ValueError):
    """La petición del cliente no cumple las reglas del protocolo de Kiro."""


class AuthError(GatewayError):
    """No hay credenciales válidas de Kiro o el refresco ha fallado."""


class UpstreamError(GatewayError):
    """El runtime de Kiro ha respondido con un fallo."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        *,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.request_id = request_id
