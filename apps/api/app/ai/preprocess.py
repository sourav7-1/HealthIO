"""Image preprocessing before OCR and the vision model (Pillow only, deterministic).

Steps: decode → apply EXIF orientation → flatten transparency → downscale to a bounded
size → greyscale + autocontrast for OCR. The vision model gets the oriented colour image
(it reads colour cues such as stamps); OCR gets the enhanced greyscale version. Every
step applied is recorded in the extraction's metadata.

Coordinates returned by OCR or the model are fractions of the oriented image, which is
also how browsers display the original (EXIF orientation applied), so regions line up.
"""

import io
from dataclasses import dataclass, field

from PIL import Image, ImageOps, UnidentifiedImageError

SUPPORTED_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_SIDE = 2000  # enough for small print; keeps model cost bounded
MIN_SIDE = 400  # below this, text is rarely legible


class UnsupportedImageError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PreparedImage:
    model_bytes: bytes  # JPEG sent to the vision model
    ocr_image: Image.Image  # greyscale, enhanced
    width: int
    height: int
    steps: list[str] = field(default_factory=list)
    media_type: str = "image/jpeg"


def prepare(data: bytes, content_type: str) -> PreparedImage:
    if content_type not in SUPPORTED_TYPES:
        raise UnsupportedImageError(
            "unsupported_type",
            "AI reading works on photos (JPEG, PNG or WebP), not this file type.",
        )
    try:
        image: Image.Image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise UnsupportedImageError(
            "unreadable_image", "The image file could not be opened."
        ) from exc

    steps = [f"decoded:{image.format or 'unknown'}:{image.width}x{image.height}"]
    oriented = ImageOps.exif_transpose(image)
    if oriented is not image:
        steps.append("exif_orientation_applied")
    image = oriented
    if image.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", image.size, "white")
        background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
        image = background
        steps.append("transparency_flattened")
    image = image.convert("RGB")

    if min(image.size) < MIN_SIDE:
        raise UnsupportedImageError(
            "too_small", "The photo is too small to read. Take a closer, sharper photo."
        )
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        steps.append(f"downscaled:{image.width}x{image.height}")

    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)  # also strips EXIF metadata (location etc.)
    steps.append("reencoded_jpeg_without_metadata")

    ocr = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
    steps.append("ocr_greyscale_autocontrast")
    return PreparedImage(
        model_bytes=buf.getvalue(),
        ocr_image=ocr,
        width=image.width,
        height=image.height,
        steps=steps,
    )
