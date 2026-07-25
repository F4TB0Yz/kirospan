#!/usr/bin/env python3
"""Install KiroSpan as a local service and global Hermes provider on macOS."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

LABEL = "dev.kirospan.gateway"
BASE_URL = "http://127.0.0.1:18789/v1"


@dataclass(frozen=True)
class InstallPaths:
    home: Path
    install_dir: Path
    venv_dir: Path
    key_file: Path
    runner: Path
    plist: Path
    stdout_log: Path
    stderr_log: Path

    @classmethod
    def for_home(cls, home: Path) -> "InstallPaths":
        install_dir = home / ".local" / "share" / "kirospan"
        return cls(
            home=home,
            install_dir=install_dir,
            venv_dir=install_dir / "venv",
            key_file=install_dir / "gateway.key",
            runner=install_dir / "run-gateway",
            plist=home / "Library" / "LaunchAgents" / f"{LABEL}.plist",
            stdout_log=home / "Library" / "Logs" / "KiroSpan.log",
            stderr_log=home / "Library" / "Logs" / "KiroSpan.error.log",
        )


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=check, text=True)


def choose_gateway_key(paths: InstallPaths, environ: dict[str, str]) -> str:
    """Reuse an explicit or installed key; otherwise generate a new secret."""
    for variable in ("KIRO_GATEWAY_KEY", "KIROSPAN_API_KEY"):
        value = environ.get(variable, "").strip()
        if value:
            return value
    if paths.key_file.is_file():
        value = paths.key_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    return secrets.token_urlsafe(32)


def write_private_file(path: Path, content: str, *, executable: bool = False) -> None:
    """Atomically write a user-private file with restrictive permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    mode = stat.S_IRUSR | stat.S_IWUSR
    if executable:
        mode |= stat.S_IXUSR
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install_runtime(paths: InstallPaths, project_root: Path) -> None:
    """Install KiroSpan into a dedicated virtual environment."""
    python = paths.venv_dir / "bin" / "python"
    if not python.exists():
        paths.install_dir.mkdir(parents=True, exist_ok=True)
        _run([sys.executable, "-m", "venv", str(paths.venv_dir)])
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            str(project_root),
        ]
    )


def configure_hermes(hermes: str, gateway_key: str) -> None:
    """Register KiroSpan globally without changing Hermes' active model."""
    settings = (
        ("providers.kirospan.name", "KiroSpan"),
        ("providers.kirospan.base_url", BASE_URL),
        ("providers.kirospan.key_env", "KIROSPAN_API_KEY"),
        ("providers.kirospan.default_model", "auto"),
        ("providers.kirospan.api_mode", "chat_completions"),
        ("providers.kirospan.discover_models", "true"),
    )
    _run([hermes, "config", "set", "KIROSPAN_API_KEY", gateway_key])
    for key, value in settings:
        _run([hermes, "config", "set", key, value])


def runner_contents(paths: InstallPaths) -> str:
    key_file = shlex.quote(str(paths.key_file))
    executable = shlex.quote(str(paths.venv_dir / "bin" / "kirospan"))
    return f"""#!/bin/sh
set -eu
key_file={key_file}
if [ ! -r "$key_file" ]; then
    echo "KiroSpan key file is missing or unreadable: $key_file" >&2
    exit 2
fi
IFS= read -r KIRO_GATEWAY_KEY < "$key_file"
if [ -z "$KIRO_GATEWAY_KEY" ]; then
    echo "KiroSpan key file is empty: $key_file" >&2
    exit 2
fi
export KIRO_GATEWAY_KEY
export KIRO_GATEWAY_HOST=127.0.0.1
export KIRO_GATEWAY_PORT=18789
exec {executable}
"""


def write_launch_agent(paths: InstallPaths) -> None:
    """Create the private runner and macOS LaunchAgent definition."""
    write_private_file(paths.runner, runner_contents(paths), executable=True)
    paths.plist.parent.mkdir(parents=True, exist_ok=True)
    paths.stdout_log.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [str(paths.runner)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 5,
        "EnvironmentVariables": {
            "HOME": str(paths.home),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        },
        "StandardOutPath": str(paths.stdout_log),
        "StandardErrorPath": str(paths.stderr_log),
    }
    temporary = paths.plist.with_name(f".{paths.plist.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as stream:
            plistlib.dump(payload, stream, sort_keys=False)
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, paths.plist)
    finally:
        temporary.unlink(missing_ok=True)


def retire_legacy_launch_agents(paths: InstallPaths, uid: int) -> list[Path]:
    """Disable known pre-KiroSpan agents that would keep port 18789 occupied."""
    retired: list[Path] = []
    domain = f"gui/{uid}"
    for label in ("com.kiro.gateway",):
        legacy_plist = paths.plist.parent / f"{label}.plist"
        if not legacy_plist.exists():
            continue
        _run(["launchctl", "bootout", domain, str(legacy_plist)], check=False)
        backup = legacy_plist.with_suffix(legacy_plist.suffix + ".disabled-by-kirospan")
        os.replace(legacy_plist, backup)
        retired.append(backup)
    return retired


def load_launch_agent(paths: InstallPaths, uid: int) -> None:
    domain = f"gui/{uid}"
    _run(["launchctl", "bootout", domain, str(paths.plist)], check=False)
    _run(["launchctl", "bootstrap", domain, str(paths.plist)])


def fetch_gateway_model_ids(gateway_key: str, timeout: float = 2.0) -> list[str]:
    request = urllib.request.Request(
        f"{BASE_URL}/models",
        headers={"Authorization": f"Bearer {gateway_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return []
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return [
        item["id"].strip()
        for item in data
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"].strip()
    ]


def hermes_config_path(hermes: str) -> Path:
    result = subprocess.run(
        [hermes, "config", "path"],
        check=True,
        text=True,
        capture_output=True,
    )
    return Path(result.stdout.strip()).expanduser()


def synchronize_hermes_catalog(
    paths: InstallPaths, hermes: str, gateway_key: str
) -> None:
    model_ids = fetch_gateway_model_ids(gateway_key)
    if not model_ids:
        print("Warning: KiroSpan model catalog could not be synchronized.", file=sys.stderr)
        return
    _run(
        [
            str(paths.venv_dir / "bin" / "python"),
            "-m",
            "gateway.hermes_config",
            "--config",
            str(hermes_config_path(hermes)),
            "--models-json",
            json.dumps(model_ids),
        ]
    )


def gateway_is_ready(gateway_key: str, timeout: float = 1.0) -> bool:
    return bool(fetch_gateway_model_ids(gateway_key, timeout=timeout))


def wait_for_gateway(gateway_key: str, attempts: int = 20) -> bool:
    for _ in range(attempts):
        if gateway_is_ready(gateway_key):
            return True
        time.sleep(0.25)
    return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install KiroSpan globally for Hermes Agent on macOS."
    )
    parser.add_argument(
        "--no-service",
        action="store_true",
        help="register the global Hermes provider but do not install launchd",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    home = Path.home()
    paths = InstallPaths.for_home(home)

    hermes = shutil.which("hermes")
    if not hermes:
        print("Error: hermes is not installed or is not on PATH.", file=sys.stderr)
        return 2
    if not shutil.which("kiro-cli"):
        print("Error: kiro-cli is not installed or is not on PATH.", file=sys.stderr)
        return 2
    if not args.no_service and sys.platform != "darwin":
        print(
            "Automatic background service installation currently requires macOS. "
            "Use --no-service and run KiroSpan with your service manager.",
            file=sys.stderr,
        )
        return 2

    gateway_key = choose_gateway_key(paths, os.environ)
    paths.install_dir.mkdir(parents=True, exist_ok=True)
    write_private_file(paths.key_file, gateway_key + "\n")

    print("Installing KiroSpan runtime...")
    install_runtime(paths, project_root)
    print("Registering KiroSpan in the global Hermes model picker...")
    configure_hermes(hermes, gateway_key)

    if args.no_service:
        if gateway_is_ready(gateway_key):
            synchronize_hermes_catalog(paths, hermes, gateway_key)
        print("\nKiroSpan is registered globally. Start the gateway separately, then run:")
        print("  hermes model --refresh")
        print("  hermes")
        return 0

    write_launch_agent(paths)
    retired_agents = retire_legacy_launch_agents(paths, os.getuid())
    for retired in retired_agents:
        print(f"Disabled legacy LaunchAgent (backup kept at {retired}).")
    load_launch_agent(paths, os.getuid())
    ready = wait_for_gateway(gateway_key)
    if ready:
        synchronize_hermes_catalog(paths, hermes, gateway_key)

    print("\nKiroSpan installation complete.")
    print("  Provider: KiroSpan (global Hermes configuration)")
    print(f"  Endpoint: {BASE_URL}")
    print(f"  Service:  {LABEL}")
    if ready:
        print("  Status:   running")
    else:
        print("  Status:   installed; not yet reachable")
        print(f"  Logs:     {paths.stderr_log}")
    print("\nSelect a Kiro model once with `hermes model`; afterwards run `hermes` anywhere.")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
