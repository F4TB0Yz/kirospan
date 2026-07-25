from __future__ import annotations

import os
import plistlib
import stat
from pathlib import Path

from scripts import install_hermes


def paths(tmp_path: Path) -> install_hermes.InstallPaths:
    return install_hermes.InstallPaths.for_home(tmp_path)


def test_choose_gateway_key_prefers_explicit_environment(tmp_path):
    install_paths = paths(tmp_path)
    install_paths.key_file.parent.mkdir(parents=True)
    install_paths.key_file.write_text("installed-key\n", encoding="utf-8")

    result = install_hermes.choose_gateway_key(
        install_paths,
        {"KIRO_GATEWAY_KEY": "explicit-key", "KIROSPAN_API_KEY": "hermes-key"},
    )

    assert result == "explicit-key"


def test_choose_gateway_key_reuses_installed_secret(tmp_path):
    install_paths = paths(tmp_path)
    install_paths.key_file.parent.mkdir(parents=True)
    install_paths.key_file.write_text("installed-key\n", encoding="utf-8")

    assert install_hermes.choose_gateway_key(install_paths, {}) == "installed-key"


def test_private_files_are_owner_only(tmp_path):
    target = tmp_path / "nested" / "secret"

    install_hermes.write_private_file(target, "secret\n")

    assert target.read_text(encoding="utf-8") == "secret\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_configure_hermes_registers_provider_without_changing_active_model(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(
        install_hermes,
        "_run",
        lambda command, check=True: commands.append(command),
    )

    install_hermes.configure_hermes("/usr/local/bin/hermes", "local-secret")

    assert commands[0] == [
        "/usr/local/bin/hermes",
        "config",
        "set",
        "KIROSPAN_API_KEY",
        "local-secret",
    ]
    configured = {command[3]: command[4] for command in commands[1:]}
    assert configured == {
        "providers.kirospan.name": "KiroSpan",
        "providers.kirospan.base_url": "http://127.0.0.1:18789/v1",
        "providers.kirospan.key_env": "KIROSPAN_API_KEY",
        "providers.kirospan.default_model": "auto",
        "providers.kirospan.api_mode": "chat_completions",
        "providers.kirospan.discover_models": "true",
    }
    assert not any(key.startswith("model.") for key in configured)


def test_launch_agent_uses_private_key_runner_and_loopback(tmp_path):
    install_paths = paths(tmp_path)

    install_hermes.write_launch_agent(install_paths)

    with install_paths.plist.open("rb") as stream:
        payload = plistlib.load(stream)
    runner = install_paths.runner.read_text(encoding="utf-8")

    assert payload["Label"] == install_hermes.LABEL
    assert payload["ProgramArguments"] == [str(install_paths.runner)]
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert str(install_paths.key_file) in runner
    assert "KIRO_GATEWAY_HOST=127.0.0.1" in runner
    assert "KIRO_GATEWAY_PORT=18789" in runner
    assert "gateway.key" not in payload.get("EnvironmentVariables", {})
    assert stat.S_IMODE(install_paths.runner.stat().st_mode) == 0o700
    assert stat.S_IMODE(install_paths.plist.stat().st_mode) == 0o600


def test_runner_does_not_embed_secret(tmp_path):
    install_paths = paths(tmp_path)
    secret = "do-not-embed-this-secret"
    install_hermes.write_private_file(install_paths.key_file, secret + "\n")

    contents = install_hermes.runner_contents(install_paths)

    assert secret not in contents
    assert "read -r KIRO_GATEWAY_KEY" in contents


def test_legacy_launch_agent_is_disabled_and_backed_up(tmp_path, monkeypatch):
    install_paths = paths(tmp_path)
    legacy = install_paths.plist.parent / "com.kiro.gateway.plist"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy", encoding="utf-8")
    commands: list[tuple[list[str], bool]] = []
    monkeypatch.setattr(
        install_hermes,
        "_run",
        lambda command, check=True: commands.append((command, check)),
    )

    retired = install_hermes.retire_legacy_launch_agents(install_paths, 501)

    backup = legacy.with_suffix(".plist.disabled-by-kirospan")
    assert retired == [backup]
    assert not legacy.exists()
    assert backup.read_text(encoding="utf-8") == "legacy"
    assert commands == [
        (["launchctl", "bootout", "gui/501", str(legacy)], False)
    ]
