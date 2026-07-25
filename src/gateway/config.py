"""Configuración del gateway, inmutable y explícita.

Toda la lectura del entorno ocurre aquí, en `from_env`, para que el resto de las
capas reciba un objeto ya validado en lugar de consultar `os.environ` por su
cuenta.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

DEFAULT_REGION = "us-east-1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18789
DEFAULT_RUNTIME_TIMEOUT = 120
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_MODEL_CACHE_TTL = 300
DEFAULT_TOKEN_SKEW_SECONDS = 120

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Rutas donde kiro-cli guarda su base de datos de credenciales.
DEFAULT_DB_PATHS = (
    Path.home() / "Library/Application Support/kiro-cli/data.sqlite3",
    Path.home() / ".local/share/kiro-cli/data.sqlite3",
)


class ConfigError(RuntimeError):
    """La configuración del entorno es inválida o insegura."""


def _int_from_env(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un entero, se recibió {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """Parámetros de ejecución del gateway."""

    gateway_key: str
    region: str = DEFAULT_REGION
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    runtime_timeout: int = DEFAULT_RUNTIME_TIMEOUT
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    model_cache_ttl: int = DEFAULT_MODEL_CACHE_TTL
    token_skew_seconds: int = DEFAULT_TOKEN_SKEW_SECONDS
    db_paths: tuple[Path, ...] = field(default=DEFAULT_DB_PATHS)

    @property
    def management_url(self) -> str:
        return f"https://management.{self.region}.kiro.dev"

    @property
    def runtime_url(self) -> str:
        return f"https://runtime.{self.region}.kiro.dev/generateAssistantResponse"

    def refresh_url(self, region: str | None = None) -> str:
        """El refresco usa la región del propio token cuando la trae."""
        return f"https://prod.{region or self.region}.auth.desktop.kiro.dev/refreshToken"

    @property
    def binds_publicly(self) -> bool:
        return self.host not in LOOPBACK_HOSTS

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "GatewayConfig":
        """Construye y valida la configuración a partir del entorno.

        El gateway puede usar credenciales locales de Kiro, por lo que todas
        las instalaciones deben definir una clave explícita, incluso cuando
        escuchan únicamente en loopback.
        """
        host = env.get("KIRO_GATEWAY_HOST", DEFAULT_HOST)
        gateway_key = env.get("KIRO_GATEWAY_KEY", "")

        if not gateway_key.strip():
            raise ConfigError(
                "KIRO_GATEWAY_KEY es obligatoria. Defina una clave local segura "
                "antes de iniciar el gateway."
            )

        return cls(
            gateway_key=gateway_key,
            region=env.get("KIRO_REGION", DEFAULT_REGION),
            host=host,
            port=_int_from_env(env, "KIRO_GATEWAY_PORT", DEFAULT_PORT),
            runtime_timeout=_int_from_env(
                env, "KIRO_RUNTIME_TIMEOUT", DEFAULT_RUNTIME_TIMEOUT
            ),
            model_cache_ttl=_int_from_env(
                env, "KIRO_MODEL_CACHE_TTL", DEFAULT_MODEL_CACHE_TTL
            ),
        )
