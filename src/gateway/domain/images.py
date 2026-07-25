"""Validación y codificación de imágenes para el runtime de Kiro.

Kiro acepta como máximo 10 imágenes por petición, cada una por debajo de 10 MB,
en cuatro formatos. Las imágenes llegan en dos dialectos distintos: bloques
`image` de Anthropic con `source.data` en base64, y bloques `image_url` de
OpenAI con data URLs. Las URLs remotas se rechazan en lugar de descargarse.
"""

import base64
import binascii
import re

from .errors import InvalidRequest

KIRO_IMAGE_FORMATS = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}
KIRO_MAX_IMAGES = 10
KIRO_MAX_IMAGE_BYTES = 10 * 1024 * 1024

_DATA_IMAGE_URL_RE = re.compile(
    r"^data:(image/[A-Za-z0-9.+-]+);base64,(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_WHITESPACE_RE = re.compile(r"\s+")


def kiro_image(media_type: str, encoded_data: str) -> dict:
    """Valida y reempaqueta una imagen en el formato que espera Kiro."""
    image_format = KIRO_IMAGE_FORMATS.get(str(media_type or "").lower())
    if not image_format:
        raise InvalidRequest("Unsupported image type. Use PNG, JPEG, GIF, or WebP.")

    compact_data = _WHITESPACE_RE.sub("", str(encoded_data or ""))
    if not compact_data:
        raise InvalidRequest("Image base64 data is empty.")
    try:
        decoded = base64.b64decode(compact_data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidRequest("Image data is not valid base64.") from exc
    if len(decoded) >= KIRO_MAX_IMAGE_BYTES:
        raise InvalidRequest("Each image must be smaller than 10 MB.")

    return {
        "format": image_format,
        "source": {"bytes": base64.b64encode(decoded).decode("ascii")},
    }


def _image_from_anthropic_block(block: dict) -> dict:
    source = block.get("source")
    if isinstance(source, dict):
        if source.get("type", "base64") != "base64":
            raise InvalidRequest("Anthropic images must use base64 sources.")
        return kiro_image(source.get("media_type", ""), source.get("data", ""))
    return kiro_image(
        block.get("mime_type") or block.get("mimeType") or block.get("media_type"),
        block.get("data", ""),
    )


def _image_from_openai_block(block: dict) -> dict:
    image_url = block.get("image_url")
    url = image_url.get("url", "") if isinstance(image_url, dict) else image_url
    match = _DATA_IMAGE_URL_RE.fullmatch(str(url or ""))
    if not match:
        raise InvalidRequest(
            "OpenAI images must use base64 data URLs; remote URLs are not fetched."
        )
    return kiro_image(match.group(1), match.group(2))


def extract_kiro_images(message: dict) -> list[dict]:
    """Extrae las imágenes de un mensaje, en ambos dialectos."""
    content = message.get("content", "")
    if not isinstance(content, list):
        return []

    images = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "image":
            images.append(_image_from_anthropic_block(block))
        elif block_type in {"image_url", "input_image"}:
            images.append(_image_from_openai_block(block))
    return images


def validate_kiro_image_count(images: list[dict]) -> list[dict]:
    if len(images) > KIRO_MAX_IMAGES:
        raise InvalidRequest("A request can include at most 10 images.")
    return images
