import io

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdfium2 import PdfiumError

from modules.ocr.base import ExtractionError, ExtractionOutput, ExtractionPipeline
from modules.ocr.mock import synthesize_fields

# Scale factor for rasterizing PDF pages before OCR: 2.0 ~= 144 DPI, a
# reasonable OCR-quality/speed tradeoff (pypdfium2's scale is relative to
# the PDF's 72-DPI canvas unit, not an absolute DPI value).
_PDF_RENDER_SCALE = 2.0

# Below this many pixels on the short side, upscale before OCR — Tesseract's
# own guidance is ~300 DPI, and a small/low-resolution scan has far fewer
# recognizable pixels per character than that. Upscaling is a crude fix
# (no new detail is created), but LANCZOS resampling measurably helps
# Tesseract's character segmentation on genuinely tiny inputs.
_MIN_OCR_DIMENSION_PX = 1000


class TesseractExtractionPipeline(ExtractionPipeline):
    """Real text extraction via local Tesseract OCR (images/PDF) or direct
    decode (text/plain) — no external OCR vendor, no API key, no per-call
    cost.

    Optional preprocessing (grayscale, contrast normalization, upscaling
    small images, and a best-effort gross-rotation fix — see
    _normalize_for_ocr/_correct_orientation below) runs before Tesseract
    sees the image, using only Pillow and Tesseract's own OSD — no new OCR
    engine, no new dependency.

    `fields` are NOT extracted from this real text — they're still the same
    deterministic synthetic values MockExtractionPipeline produces (shared
    via modules.ocr.mock.synthesize_fields, not duplicated), clearly a
    placeholder pending a real field-extraction backend (e.g. LLM-based).
    Only `raw_text` and `confidence` are real here. This also means the
    "no real PHI" project constraint now depends on what gets uploaded, not
    on the pipeline ignoring it — see docs/adr for the tradeoff.
    """

    def __init__(
        self,
        *,
        max_pdf_pages: int = 50,
        preprocessing_enabled: bool = True,
        psm: int = 3,
    ) -> None:
        self._max_pdf_pages = max_pdf_pages
        self._preprocessing_enabled = preprocessing_enabled
        self._psm = psm

    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
        if not data:
            return ExtractionOutput(raw_text="", fields={}, confidence=0.0)

        if content_type == "text/plain":
            raw_text, confidence = data.decode("utf-8", errors="replace"), 1.0
        elif content_type in ("image/png", "image/jpeg"):
            raw_text, confidence = self._ocr_image(
                data, preprocess=self._preprocessing_enabled, psm=self._psm
            )
        elif content_type == "application/pdf":
            raw_text, confidence = self._ocr_pdf(
                data,
                max_pages=self._max_pdf_pages,
                preprocess=self._preprocessing_enabled,
                psm=self._psm,
            )
        else:
            raise ValueError(f"unsupported content type for OCR: {content_type}")

        fields = synthesize_fields(data, content_type)
        return ExtractionOutput(raw_text=raw_text, fields=fields, confidence=confidence)

    @staticmethod
    def _ocr_image(data: bytes, *, preprocess: bool = True, psm: int = 3) -> tuple[str, float]:
        try:
            with Image.open(io.BytesIO(data)) as image:
                return _ocr_pil_image(image, preprocess=preprocess, psm=psm)
        except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
            # DecompressionBombError isn't a subclass of
            # UnidentifiedImageError (verified) — a huge-pixel-count image
            # (accidental or a deliberate "image bomb") would otherwise
            # propagate uncaught, same failure class as ADR-0012.
            raise ExtractionError(f"could not read image data: {exc}") from exc

    @staticmethod
    def _ocr_pdf(
        data: bytes, *, max_pages: int, preprocess: bool = True, psm: int = 3
    ) -> tuple[str, float]:
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
                    text, confidence = _ocr_pil_image(
                        bitmap.to_pil(), preprocess=preprocess, psm=psm
                    )
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


def _correct_orientation(image: Image.Image) -> Image.Image:
    """Best-effort gross-rotation fix (0/90/180/270) via Tesseract's own
    orientation-and-script detection (OSD) — catches a page scanned or
    photographed sideways/upside-down.

    Never fatal: OSD reliably fails on sparse/blank/low-text images (too
    little text to detect orientation from) and raises if Tesseract itself
    isn't installed at all — both are caught here and simply skip the
    correction, since this is an OCR-quality enhancement, not a required
    step. Does NOT address sub-degree skew (a slightly tilted scan) — that
    needs angle detection this project has no dependency for (no
    numpy/opencv); a named, deliberate limitation, not an oversight.
    """
    try:
        osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
        rotation = int(osd.get("rotate", 0)) % 360
    except (pytesseract.TesseractError, pytesseract.TesseractNotFoundError):
        return image
    if rotation:
        return image.rotate(-rotation, expand=True)
    return image


def _normalize_for_ocr(image: Image.Image) -> Image.Image:
    """Grayscale + contrast normalization + upscaling for small images —
    plain Pillow operations (no new dependency), aimed at the low-
    resolution and low-contrast/noisy-scan cases Tesseract struggles with
    most.
    """
    image = image.convert("L")
    image = ImageOps.autocontrast(image)
    short_side = min(image.size)
    if 0 < short_side < _MIN_OCR_DIMENSION_PX:
        scale = _MIN_OCR_DIMENSION_PX / short_side
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)),
            Image.Resampling.LANCZOS,
        )
    return image


def _ocr_pil_image(
    image: Image.Image, *, preprocess: bool = True, psm: int = 3
) -> tuple[str, float]:
    if preprocess:
        image = _correct_orientation(image)
        image = _normalize_for_ocr(image)

    ocr_data = pytesseract.image_to_data(
        image, output_type=pytesseract.Output.DICT, config=f"--psm {psm}"
    )

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
