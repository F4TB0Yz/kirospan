from __future__ import annotations

import stat
from pathlib import Path

import yaml

from gateway.hermes_config import migrate_hermes_config


def test_migration_removes_legacy_provider_and_preserves_active_model(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """model:
  default: deepseek-v4-flash
  provider: opencode-go
providers:
  kiro-gateway:
    name: Old provider map
    base_url: http://localhost:8000/v1
  kirospan:
    name: KiroSpan
    base_url: http://127.0.0.1:18789/v1
    default_model: auto
    discover_models: true
custom_providers:
- name: kiro-gateway
  base_url: http://localhost:8000/v1
  api_key: old-secret
- name: Keep Me
  base_url: http://127.0.0.1:9999/v1
""",
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    (tmp_path / "provider_models_cache.json").write_text("{}", encoding="utf-8")
    legacy_profile = tmp_path / "profiles" / "kiro-gateway"
    legacy_profile.mkdir(parents=True)
    (legacy_profile / "config.yaml").write_text("legacy", encoding="utf-8")

    result = migrate_hermes_config(
        config_path,
        ["auto", "gpt-5.6-terra", "claude-sonnet-5", "gpt-5.6-terra"],
    )

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["model"] == {
        "default": "deepseek-v4-flash",
        "provider": "opencode-go",
    }
    assert "kiro-gateway" not in config["providers"]
    assert config["providers"]["kirospan"]["models"] == [
        "auto",
        "gpt-5.6-terra",
        "claude-sonnet-5",
    ]
    assert [entry["name"] for entry in config["custom_providers"]] == ["Keep Me"]
    assert not (tmp_path / "provider_models_cache.json").exists()
    assert not legacy_profile.exists()
    assert (tmp_path / "profiles" / "kiro-gateway.disabled-by-kirospan").exists()
    assert config_path.with_suffix(".yaml.bak-kirospan").exists()
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert result["models"] == 3
    assert len(result["removed"]) == 3


def test_migration_removes_empty_custom_provider_list(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "custom_providers:\n- name: kiro-gateway\n  base_url: http://localhost:8000/v1\n",
        encoding="utf-8",
    )

    migrate_hermes_config(config_path, ["auto"])

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "custom_providers" not in config
    assert config["providers"]["kirospan"]["models"] == ["auto"]
