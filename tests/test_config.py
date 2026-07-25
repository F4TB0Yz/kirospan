"""Tests unitarios de :mod:`gateway.config`."""

from pathlib import Path

import pytest

from gateway.config import DEFAULT_PORT, DEFAULT_REGION, ConfigError, GatewayConfig


class TestDefaults:
    def test_defaults_use_loopback_when_a_key_is_provided(self):
        config = GatewayConfig.from_env({"KIRO_GATEWAY_KEY": "test-key"})
        assert config.host == "127.0.0.1"
        assert config.port == DEFAULT_PORT
        assert config.region == DEFAULT_REGION
        assert config.gateway_key == "test-key"
        assert config.binds_publicly is False

    def test_values_are_read_from_the_environment(self):
        config = GatewayConfig.from_env({
            "KIRO_REGION": "eu-west-1",
            "KIRO_GATEWAY_HOST": "0.0.0.0",
            "KIRO_GATEWAY_PORT": "9000",
            "KIRO_GATEWAY_KEY": "test-key",
            "KIRO_RUNTIME_TIMEOUT": "45",
            "KIRO_MODEL_CACHE_TTL": "60",
        })
        assert config.region == "eu-west-1"
        assert config.host == "0.0.0.0"
        assert config.port == 9000
        assert config.gateway_key == "test-key"
        assert config.runtime_timeout == 45
        assert config.model_cache_ttl == 60

    def test_empty_string_uses_the_default(self):
        config = GatewayConfig.from_env({
            "KIRO_GATEWAY_KEY": "test-key",
            "KIRO_GATEWAY_PORT": "",
        })
        assert config.port == DEFAULT_PORT

    def test_non_numeric_port_is_rejected(self):
        with pytest.raises(ConfigError, match="debe ser un entero"):
            GatewayConfig.from_env({
                "KIRO_GATEWAY_KEY": "test-key",
                "KIRO_GATEWAY_PORT": "ocho-mil",
            })


class TestAuthenticationRequired:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "0.0.0.0"])
    def test_missing_key_is_rejected_for_every_bind(self, host):
        with pytest.raises(ConfigError, match="KIRO_GATEWAY_KEY es obligatoria"):
            GatewayConfig.from_env({"KIRO_GATEWAY_HOST": host})

    def test_whitespace_only_key_is_rejected(self):
        with pytest.raises(ConfigError, match="KIRO_GATEWAY_KEY es obligatoria"):
            GatewayConfig.from_env({"KIRO_GATEWAY_KEY": "   "})

    def test_public_bind_with_key_is_allowed(self):
        config = GatewayConfig.from_env({
            "KIRO_GATEWAY_HOST": "0.0.0.0",
            "KIRO_GATEWAY_KEY": "test-key",
        })
        assert config.binds_publicly is True


class TestUrls:
    def test_urls_derive_from_the_region(self):
        config = GatewayConfig(gateway_key="k", region="ap-south-1")
        assert config.management_url == "https://management.ap-south-1.kiro.dev"
        assert config.runtime_url == (
            "https://runtime.ap-south-1.kiro.dev/generateAssistantResponse"
        )

    def test_refresh_url_defaults_to_the_configured_region(self):
        config = GatewayConfig(gateway_key="k", region="us-east-1")
        assert config.refresh_url() == (
            "https://prod.us-east-1.auth.desktop.kiro.dev/refreshToken"
        )

    def test_refresh_url_honours_the_token_region(self):
        config = GatewayConfig(gateway_key="k", region="us-east-1")
        assert "eu-west-1" in config.refresh_url("eu-west-1")


class TestImmutability:
    def test_config_cannot_be_mutated(self):
        config = GatewayConfig(gateway_key="k")
        with pytest.raises(AttributeError):
            config.region = "otra"

    def test_db_paths_are_a_tuple(self):
        config = GatewayConfig(gateway_key="k")
        assert isinstance(config.db_paths, tuple)
        assert all(isinstance(path, Path) for path in config.db_paths)
