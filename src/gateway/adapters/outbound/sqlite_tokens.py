"""Persistencia del token en la SQLite de kiro-cli.

El gateway lee y escribe la misma fila que usa el propio `kiro-cli`, así que hay
dos escritores sobre el mismo fichero: las escrituras van con timeout y se
mantienen lo más cortas posible. `sqlite3` es síncrono, así que cada operación
se delega a un hilo para no bloquear el event loop.

Nota: kiro-cli almacena el token real en el macOS Keychain y puede dejar un
placeholder en SQLite. Este módulo incluye un fallback al Keychain cuando el
token de SQLite parece inválido.
"""

import asyncio
import json
import logging
import sqlite3
import subprocess
from pathlib import Path

TOKEN_KEY = "kirocli:social:token"
WRITE_TIMEOUT_SECONDS = 10
# Tokens reales de Kiro tienen access_token > 50 chars (JWT)
MIN_VALID_TOKEN_LENGTH = 50

log = logging.getLogger("kirospan")


def _read_keychain_token() -> dict | None:
    """Lee el token del macOS Keychain (donde kiro-cli lo guarda realmente)."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", TOKEN_KEY, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return json.loads(result.stdout.strip())
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, OSError) as exc:
        log.debug(f"Keychain read failed: {exc}")
        return None


def _token_looks_valid(token: dict | None) -> bool:
    """Heurística: un token real tiene access_token largo (JWT)."""
    if not token:
        return False
    at = token.get("access_token", "")
    return len(at) >= MIN_VALID_TOKEN_LENGTH


class SqliteTokenRepository:
    """Implementa `TokenRepository` sobre la base de datos de kiro-cli."""

    def __init__(self, db_paths: tuple[Path, ...]) -> None:
        self._db_paths = db_paths

    def _find_db(self) -> Path | None:
        for path in self._db_paths:
            if path and path.exists():
                return path
        return None

    def location_path(self) -> Path | None:
        """Ruta del almacén como `Path`, para quien necesite el objeto."""
        return self._find_db()

    def location(self) -> str | None:
        path = self._find_db()
        return str(path) if path else None

    def _load_sync(self) -> dict | None:
        db_path = self._find_db()
        if not db_path:
            log.error("kiro-cli DB not found. Run 'kiro-cli auth login' first.")
            return _read_keychain_token()
        try:
            connection = sqlite3.connect(str(db_path))
            try:
                row = connection.execute(
                    "SELECT value FROM auth_kv WHERE key = ?", (TOKEN_KEY,)
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            log.warning(f"Failed to read kiro-cli token: {exc}")
            return _read_keychain_token()

        if not row:
            return _read_keychain_token()
        try:
            token = json.loads(row[0])
        except (TypeError, json.JSONDecodeError) as exc:
            log.warning(f"Failed to parse kiro-cli token: {exc}")
            return _read_keychain_token()

        # kiro-cli stores the real token in macOS Keychain; SQLite may have
        # a placeholder. Fall back to Keychain if the SQLite token looks dummy.
        if not _token_looks_valid(token):
            log.info("SQLite token looks invalid (too short), trying macOS Keychain...")
            keychain_token = _read_keychain_token()
            if _token_looks_valid(keychain_token):
                # Sync keychain token back to SQLite for future reads
                try:
                    self._save_sync(keychain_token)
                    log.info("Synced valid token from Keychain to SQLite")
                except Exception as exc:
                    log.debug(f"Failed to sync keychain token to SQLite: {exc}")
                return keychain_token
            # Return whatever we have
            return token

        return token

    def _save_sync(self, token: dict) -> None:
        db_path = self._find_db()
        if not db_path:
            raise RuntimeError("kiro-cli DB not found")
        connection = sqlite3.connect(str(db_path), timeout=WRITE_TIMEOUT_SECONDS)
        try:
            connection.execute(
                "UPDATE auth_kv SET value = ? WHERE key = ?",
                (json.dumps(token), TOKEN_KEY),
            )
            connection.commit()
        finally:
            connection.close()

    async def load(self) -> dict | None:
        return await asyncio.to_thread(self._load_sync)

    async def save(self, token: dict) -> None:
        await asyncio.to_thread(self._save_sync, token)

    # Variantes síncronas: las usa el shim de compatibilidad, cuyos seams
    # históricos son funciones síncronas que los tests legacy parchean.
    def load_sync(self) -> dict | None:
        return self._load_sync()

    def save_sync(self, token: dict) -> None:
        self._save_sync(token)
