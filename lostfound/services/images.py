"""Untrusted image handling with Pillow.

Every upload is decoded by Pillow (the extension and client content-type are
ignored), checked against an allow-list of real formats and a pixel limit,
auto-rotated, downscaled, and re-encoded as a fresh JPEG/PNG. Re-encoding
without passing ``exif=`` drops all metadata, including GPS coordinates."""
import base64
import io
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_PIXELS = 40_000_000
MAX_DIMENSION = 1600


def process_upload(uploaded, *, max_dimension=MAX_DIMENSION, keep_png=False) -> ContentFile:
    size = getattr(uploaded, "size", None)
    if size is not None and size > settings.MAX_IMAGE_UPLOAD_BYTES:
        raise ValidationError(f"Images must be smaller than {settings.MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        uploaded.seek(0)
        probe = Image.open(uploaded)
        fmt = probe.format
        if fmt not in ALLOWED_FORMATS:
            raise ValidationError("Upload a JPEG, PNG or WebP photo.")
        if probe.width * probe.height > MAX_PIXELS:
            raise ValidationError("That image is too large to process.")
        probe.verify()  # structural check; image must be reopened afterwards
        uploaded.seek(0)
        img = Image.open(uploaded)
        img.load()
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ValidationError("That file is not a valid image.")

    img = ImageOps.exif_transpose(img)
    img.thumbnail((max_dimension, max_dimension))
    out = io.BytesIO()
    if keep_png:
        img = img.convert("RGBA")
        img.save(out, format="PNG", optimize=True)
        ext = "png"
    else:
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
        img.save(out, format="JPEG", quality=85, optimize=True)
        ext = "jpg"
    return ContentFile(out.getvalue(), name=f"{uuid.uuid4().hex}.{ext}")


def signature_from_data_url(data_url: str) -> ContentFile:
    """Decode a canvas signature (data:image/png;base64,...) and re-encode it."""
    prefix = "data:image/png;base64,"
    if not data_url or not data_url.startswith(prefix) or len(data_url) > 2_000_000:
        raise ValidationError("A drawn signature is required.")
    try:
        raw = base64.b64decode(data_url[len(prefix):], validate=True)
    except (ValueError, TypeError):
        raise ValidationError("The signature could not be read.")
    processed = process_upload(ContentFile(raw, name="signature.png"), max_dimension=800, keep_png=True)
    img = Image.open(io.BytesIO(processed.read())).convert("LA")
    processed.seek(0)
    # Reject blank canvases: a real signature has some opaque dark pixels.
    dark = sum(1 for lum, alpha in img.getdata() if alpha > 0 and lum < 128)
    if dark < 30:
        raise ValidationError("The signature pad is empty.")
    return processed
