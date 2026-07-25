"""Tests unitarios de `gateway.domain`, sin red ni parcheo de módulos."""

import base64

import pytest

from gateway.domain.errors import InvalidRequest
from gateway.domain.images import (
    KIRO_MAX_IMAGES,
    extract_kiro_images,
    kiro_image,
    validate_kiro_image_count,
)
from gateway.domain.model_id import normalize_model_id, sanitize_model_id
from gateway.domain.token import (
    access_token_of,
    profile_arn_of,
    refresh_token_of,
    token_expires_soon,
)

PNG_BASE64 = base64.b64encode(b"fake-png-bytes").decode("ascii")


class TestModelId:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("claude-fable-5", "gpt-5.6-sol"),
            ("fable", "gpt-5.6-sol"),
            ("sonnet", "claude-sonnet-5"),
            ("opus", "claude-opus-5"),
            ("haiku", "claude-haiku-4.5"),
            ("claude-opus-4-8", "claude-opus-4.8"),
            ("claude-sonnet-4-5-20250101", "claude-sonnet-4.5"),
            ("claude-sonnet-5", "claude-sonnet-5"),
        ],
    )
    def test_aliases_and_version_separator(self, raw, expected):
        assert normalize_model_id(raw) == expected

    def test_claude_code_context_modifier_is_stripped(self):
        assert normalize_model_id("claude-fable-5[1m]") == "gpt-5.6-sol"
        assert normalize_model_id("claude-opus-4-8[1m]") == "claude-opus-4.8"

    def test_ansi_and_dangling_sgr_are_stripped(self):
        assert normalize_model_id("\x1b[1mclaude-opus-4-8\x1b[0m") == "claude-opus-4.8"
        assert normalize_model_id("claude-fable-5[22m") == "gpt-5.6-sol"

    def test_sanitize_keeps_case_and_trims_control_chars(self):
        assert sanitize_model_id("  \x00Claude-Sonnet-5\x7f ") == "Claude-Sonnet-5"

    def test_empty_and_none_are_tolerated(self):
        assert normalize_model_id("") == ""
        assert normalize_model_id(None) == ""

    def test_alias_table_can_be_injected(self):
        assert normalize_model_id("mio", aliases={"mio": "claude-opus-5"}) == "claude-opus-5"


class TestToken:
    def test_missing_expiry_is_treated_as_expired(self):
        assert token_expires_soon({}) is True

    def test_malformed_expiry_is_treated_as_expired(self):
        assert token_expires_soon({"expires_at": "no-es-una-fecha"}) is True

    def test_expired_token(self):
        assert token_expires_soon({"expires_at": "2000-01-01T00:00:00Z"}) is True

    def test_token_far_in_the_future_is_valid(self):
        assert token_expires_soon({"expires_at": "2099-01-01T00:00:00Z"}) is False

    def test_skew_window_marks_token_as_expiring(self):
        # 60 s de margen real, con un skew de 120 s el token ya cuenta como expirado.
        token = {"expires_at": "2026-01-01T00:01:00Z"}
        reference = 1767225600.0  # 2026-01-01T00:00:00Z
        assert token_expires_soon(token, skew_seconds=120, now=reference) is True
        assert token_expires_soon(token, skew_seconds=30, now=reference) is False

    def test_naive_timestamps_are_interpreted_as_utc(self):
        assert token_expires_soon({"expires_at": "2099-01-01T00:00:00"}) is False

    def test_camel_case_field_names_are_accepted(self):
        assert token_expires_soon({"expiresAt": "2099-01-01T00:00:00Z"}) is False
        assert access_token_of({"accessToken": "abc"}) == "abc"
        assert refresh_token_of({"refreshToken": "def"}) == "def"
        assert profile_arn_of({"profileArn": "arn:aws:x"}) == "arn:aws:x"

    def test_snake_case_takes_precedence_when_both_present(self):
        assert access_token_of({"access_token": "snake", "accessToken": "camel"}) == "snake"


class TestImages:
    def test_valid_png_is_reencoded(self):
        image = kiro_image("image/png", PNG_BASE64)
        assert image["format"] == "png"
        assert base64.b64decode(image["source"]["bytes"]) == b"fake-png-bytes"

    def test_jpg_maps_to_jpeg(self):
        assert kiro_image("image/JPG", PNG_BASE64)["format"] == "jpeg"

    def test_unsupported_media_type_is_rejected(self):
        with pytest.raises(InvalidRequest, match="Unsupported image type"):
            kiro_image("image/tiff", PNG_BASE64)

    def test_empty_data_is_rejected(self):
        with pytest.raises(InvalidRequest, match="empty"):
            kiro_image("image/png", "   ")

    def test_invalid_base64_is_rejected(self):
        with pytest.raises(InvalidRequest, match="not valid base64"):
            kiro_image("image/png", "no-base64-!!!")

    def test_oversized_image_is_rejected(self):
        oversized = base64.b64encode(b"x" * (10 * 1024 * 1024)).decode("ascii")
        with pytest.raises(InvalidRequest, match="smaller than 10 MB"):
            kiro_image("image/png", oversized)

    def test_anthropic_block_is_extracted(self):
        message = {
            "content": [
                {"type": "text", "text": "mira"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": PNG_BASE64},
                },
            ]
        }
        assert [image["format"] for image in extract_kiro_images(message)] == ["png"]

    def test_anthropic_non_base64_source_is_rejected(self):
        message = {
            "content": [
                {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}}
            ]
        }
        with pytest.raises(InvalidRequest, match="base64 sources"):
            extract_kiro_images(message)

    def test_openai_data_url_is_extracted(self):
        message = {
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/webp;base64,{PNG_BASE64}"},
                }
            ]
        }
        assert [image["format"] for image in extract_kiro_images(message)] == ["webp"]

    def test_openai_remote_url_is_rejected_without_fetching(self):
        message = {
            "content": [
                {"type": "image_url", "image_url": {"url": "https://example.com/private.png"}}
            ]
        }
        with pytest.raises(InvalidRequest, match="remote URLs are not fetched"):
            extract_kiro_images(message)

    def test_plain_text_content_yields_no_images(self):
        assert extract_kiro_images({"content": "solo texto"}) == []

    def test_image_count_cap(self):
        images = [{"format": "png"}] * KIRO_MAX_IMAGES
        assert validate_kiro_image_count(images) == images
        with pytest.raises(InvalidRequest, match="at most 10 images"):
            validate_kiro_image_count(images + [{"format": "png"}])

    def test_invalid_request_is_a_value_error(self):
        # La capa HTTP traduce ValueError a 400 y los tests legacy dependen de ello.
        assert issubclass(InvalidRequest, ValueError)
