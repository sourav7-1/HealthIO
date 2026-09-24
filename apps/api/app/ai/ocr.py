"""OCR engines. OCR text is a second, independent reading used to check the vision model
(AI_SAFETY.md §4.5) and to locate evidence on the page; it is never trusted on its own."""

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from typing import Protocol

from PIL import Image

from app.ai.schemas import Region


@dataclass(frozen=True)
class OcrWord:
    text: str
    confidence: float  # 0-1
    region: Region


@dataclass
class OcrResult:
    engine: str
    text: str = ""
    words: list[OcrWord] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.words)


class OcrEngine(Protocol):
    name: str

    async def read(self, image: Image.Image) -> OcrResult: ...


class NoOcr:
    name = "none"

    async def read(self, image: Image.Image) -> OcrResult:
        return OcrResult(engine=self.name)


class TesseractOcr:
    """Tesseract via pytesseract (English + Hindi when the language data is installed)."""

    name = "tesseract"

    def __init__(self, languages: str = "eng") -> None:
        self.languages = languages

    @staticmethod
    def installed() -> bool:
        return shutil.which("tesseract") is not None

    async def read(self, image: Image.Image) -> OcrResult:
        import pytesseract  # imported lazily: optional at runtime

        def _run() -> OcrResult:
            data = pytesseract.image_to_data(
                image, lang=self.languages, output_type=pytesseract.Output.DICT
            )
            words: list[OcrWord] = []
            width, height = image.size
            for i, text in enumerate(data["text"]):
                text = text.strip()
                conf = float(data["conf"][i])
                if not text or conf < 0:
                    continue
                words.append(
                    OcrWord(
                        text=text,
                        confidence=max(0.0, min(1.0, conf / 100)),
                        region=Region(
                            x=data["left"][i] / width,
                            y=data["top"][i] / height,
                            w=max(data["width"][i], 1) / width,
                            h=max(data["height"][i], 1) / height,
                        ),
                    )
                )
            return OcrResult(engine=self.name, text=" ".join(w.text for w in words), words=words)

        return await asyncio.to_thread(_run)


def build_ocr(engine: str) -> OcrEngine:
    if engine == "tesseract" and TesseractOcr.installed():
        return TesseractOcr()
    return NoOcr()


_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str | None) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def locate(ocr: OcrResult, evidence: str | None) -> tuple[Region | None, float | None]:
    """Find the evidence text among OCR words; returns the union box and the lowest word
    confidence, or (None, None) when the words are not all found in sequence."""
    wanted = tokens(evidence)
    if not wanted or not ocr.words:
        return None, None
    word_tokens = [tokens(w.text) for w in ocr.words]
    flat: list[tuple[str, int]] = [(t, i) for i, ts in enumerate(word_tokens) for t in ts]
    n = len(wanted)
    for start in range(len(flat) - n + 1):
        if [t for t, _ in flat[start : start + n]] == wanted:
            idx = sorted({i for _, i in flat[start : start + n]})
            boxes = [ocr.words[i].region for i in idx]
            x0 = min(b.x for b in boxes)
            y0 = min(b.y for b in boxes)
            x1 = max(b.x + b.w for b in boxes)
            y1 = max(b.y + b.h for b in boxes)
            region = Region(x=x0, y=y0, w=max(x1 - x0, 1e-3), h=max(y1 - y0, 1e-3))
            return region, min(ocr.words[i].confidence for i in idx)
    return None, None
