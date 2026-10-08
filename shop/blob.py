import secrets

import vercel.blob
from django.utils.text import slugify
from PIL import Image, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}
MAX_PIXELS = 40_000_000
MAX_BYTES = 4 * 1024 * 1024  # under Vercel's 4.5 MB request body limit


class ImageUploadError(Exception):
    pass


def validate_image(uploaded) -> tuple[str, str]:
    if uploaded.size > MAX_BYTES:
        raise ImageUploadError("A foto deve ter no máximo 4 MB.")
    try:
        with Image.open(uploaded) as image:
            image_format = image.format
            too_big = image.width * image.height > MAX_PIXELS
            if not too_big:
                image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageUploadError("Arquivo de imagem inválido.") from None
    finally:
        uploaded.seek(0)
    if too_big:
        raise ImageUploadError("A foto é grande demais (máx. 40 megapixels).")
    if image_format not in ALLOWED_FORMATS:
        raise ImageUploadError("Use uma foto JPG, PNG ou WebP.")
    return ALLOWED_FORMATS[image_format]


def upload_product_image(uploaded, product_name: str) -> str:
    """Upload to the project's public Blob store (BLOB_READ_WRITE_TOKEN) and return the public URL."""
    ext, content_type = validate_image(uploaded)
    path = f"products/{slugify(product_name) or 'produto'}-{secrets.token_hex(4)}.{ext}"
    result = vercel.blob.put(path, uploaded.read(), access="public", content_type=content_type)
    return result.url
