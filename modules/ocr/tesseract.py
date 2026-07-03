import io

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, UnidentifiedImageError
from pypdfium2 import PdfiumError

from modules.ocr.base import ExtractionError, ExtractionOutput, ExtractionPipeline
from modules.ocr.mock import synthesize_fields

# Scale factor for rasterizing PDF pages before OCR: 2.0 ~= 144 DPI, a
# reasonable OCR-quality/speed tradeoff (pypdfium2's scale is relative to
# the PDF's 72-DPI canvas unit, not an absolute DPI value).
_PDF_RENDER_SCALE = 2.0


class TesseractExtractionPipeline(ExtractionPipeline):
    """Real text extraction via local Tesseract OCR (images/PDF) or direct
    decode (text/plain) — no external OCR vendor, no API key, no per-call
    cost, no image-preprocessing beyond basic PDF rasterization.

    `fields` are NOT extracted from this real text — they're still the same
    deterministic synthetic values MockExtractionPipeline produces (shared
    via modules.ocr.mock.synthesize_fields, not duplicated), clearly a
    placeholder pending a real field-extraction backend (e.g. LLM-based).
    Only `raw_text` and `confidence` are real here. This also means the
    "no real PHI" project constraint now depends on what gets uploaded, not
    on the pipeline ignoring it — see docs/adr for the tradeoff.
    """

    def __init__(self, *, max_pdf_pages: int = 50) -> None:
        self._max_pdf_pages = max_pdf_pages

    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
        if not data:
            return ExtractionOutput(raw_text="", fields={}, confidence=0.0)

        if content_type == "text/plain":
            raw_text, confidence = data.decode("utf-8", errors="replace"), 1.0
        elif content_type in ("image/png", "image/jpeg"):
            raw_text, confidence = self._ocr_image(data)
        elif content_type == "application/pdf":
            raw_text, confidence = self._ocr_pdf(data, max_pages=self._max_pdf_pages)
        else:
            raise ValueError(f"unsupported content type for OCR: {content_type}")

        fields = synthesize_fields(data, content_type)
        return ExtractionOutput(raw_text=raw_text, fields=fields, confidence=confidence)

    @staticmethod
    def _ocr_image(data: bytes) -> tuple[str, float]:
        try:
            with Image.open(io.BytesIO(data)) as image:
                return _ocr_pil_image(image)
        except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
            # DecompressionBombError isn't a subclass of
            # UnidentifiedImageError (verified) — a huge-pixel-count image
            # (accidental or a deliberate "image bomb") would otherwise
            # propagate uncaught, same failure class as ADR-0012.
            raise ExtractionError(f"could not read image data: {exc}") from exc

    @staticmethod
    def _ocr_pdf(data: bytes, *, max_pages: int) -> tuple[str, float]:
        try:
            pdf = pdfium.PdfDocument(data)
        except PdfiumError as exc:
            raise ExtractionError(f"could not read PDF data: {exc}") from exc
        try:
            page_count = len(pdf)
            if page_count > max_pages:
                raise ExtractionError(
                    f"PDF has {page_count} pages, exceeding the {max_pages}-page limit"
                )
            page_texts: list[str] = []
            confidences: list[float] = []
            for page in pdf:
                bitmap = page.render(scale=_PDF_RENDER_SCALE)
                try:
                    text, confidence = _ocr_pil_image(bitmap.to_pil())
                finally:
                    bitmap.close()
                if text.strip():
                    page_texts.append(text)
                    confidences.append(confidence)
            raw_text = "\n\n".join(page_texts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            return raw_text, avg_confidence
        finally:
            pdf.close()


def _ocr_pil_image(image: Image.Image) -> tuple[str, float]:
    ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    words: list[str] = []
    confidences: list[float] = []
    for text, conf in zip(ocr_data["text"], ocr_data["conf"], strict=True):
        stripped = text.strip()
        if not stripped:
            continue
        words.append(stripped)
        conf_value = float(conf)
        if conf_value >= 0:  # tesseract uses -1 for non-text regions
            confidences.append(conf_value)

    raw_text = " ".join(words)
    avg_confidence = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0
    return raw_text, avg_confidence
