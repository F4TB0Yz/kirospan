"""Safe migration helpers for KiroSpan's global Hermes configuration."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
from pathlib import Path

import yaml


def _slug(value: object) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def migrate_hermes_config(config_path: Path, model_ids: list[str]) -> dict[str, object]:
    """Remove the legacy provider and persist KiroSpan's live model fallback."""
    config_path = config_path.expanduser().resolve()
    raw = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    config = yaml.safe_load(raw) if raw else {}
    if not isinstance(config, dict):
        raise ValueError(f"Hermes config must contain a mapping: {config_path}")

    backup = config_path.with_suffix(config_path.suffix + ".bak-kirospan")
    if config_path.exists() and not backup.exists():
        shutil.copy2(config_path, backup)

    removed: list[str] = []
    providers = config.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    for key in list(providers):
        if _slug(key) == "kiro-gateway":
            providers.pop(key)
            removed.append(f"providers.{key}")

    kirospan = providers.get("kirospan")
    if not isinstance(kirospan, dict):
        kirospan = {}
    unique_models = list(dict.fromkeys(model for model in model_ids if model))
    if unique_models:
        kirospan["models"] = unique_models
    providers["kirospan"] = kirospan
    config["providers"] = providers

    custom = config.get("custom_providers")
    if isinstance(custom, list):
        kept = []
        for entry in custom:
            if isinstance(entry, dict) and _slug(entry.get("name")) == "kiro-gateway":
                removed.append("custom_providers.kiro-gateway")
                continue
            kept.append(entry)
        if kept:
            config["custom_providers"] = kept
        else:
            config.pop("custom_providers", None)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(config_path.stat().st_mode) if config_path.exists() else 0o600
    temporary = config_path.with_name(f".{config_path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        os.chmod(temporary, mode)
        os.replace(temporary, config_path)
    finally:
        temporary.unlink(missing_ok=True)

    cache = config_path.parent / "provider_models_cache.json"
    cache.unlink(missing_ok=True)

    legacy_profile = config_path.parent / "profiles" / "kiro-gateway"
    disabled_profile = legacy_profile.with_name("kiro-gateway.disabled-by-kirospan")
    if legacy_profile.exists() and not disabled_profile.exists():
        legacy_profile.rename(disabled_profile)
        removed.append("profiles/kiro-gateway")

    return {
        "models": len(unique_models),
        "removed": removed,
        "backup": str(backup) if backup.exists() else "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--models-json", required=True)
    args = parser.parse_args(argv)
    models = json.loads(args.models_json)
    if not isinstance(models, list) or not all(isinstance(item, str) for item in models):
        raise SystemExit("--models-json must be a JSON string array")
    result = migrate_hermes_config(args.config, models)
    print(
        f"Hermes catalog synchronized: {result['models']} KiroSpan models; "
        f"{len(result['removed'])} legacy entries removed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
