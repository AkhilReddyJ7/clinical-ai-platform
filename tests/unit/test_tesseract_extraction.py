import io
from unittest.mock import patch

import pypdfium2 as pdfium
import pytest
from PIL import Image

from modules.ocr.base import ExtractionError
from modules.ocr.mock import synthesize_fields
from modules.ocr.tesseract import TesseractExtractionPipeline, _ocr_pil_image

_UNLIMITED_PAGES = 10_000


def test_empty_data_returns_empty_output() -> None:
    pipeline = TesseractExtractionPipeline()
    output = pipeline.extract(data=b"", content_type="text/plain")
    assert output.raw_text == ""
    assert output.fields == {}
    assert output.confidence == 0.0


def test_text_plain_is_direct_passthrough_with_full_confidence() -> None:
    pipeline = TesseractExtractionPipeline()
    data = b"synthetic clinical note content"

    output = pipeline.extract(data=data, content_type="text/plain")

    assert output.raw_text == "synthetic clinical note content"
    assert output.confidence == 1.0
    assert output.fields == synthesize_fields(data, "text/plain")


def test_unsupported_content_type_raises_value_error() -> None:
    pipeline = TesseractExtractionPipeline()
    with pytest.raises(ValueError):
        pipeline.extract(data=b"data", content_type="application/octet-stream")


def test_corrupted_image_raises_extraction_error_not_a_crash() -> None:
    # No real image decoding ever starts — PIL rejects the bytes before
    # anything touches tesseract, so no binary is needed for this test.
    pipeline = TesseractExtractionPipeline()
    with pytest.raises(ExtractionError):
        pipeline.extract(data=b"not actually a png", content_type="image/png")


def test_corrupted_pdf_raises_extraction_error_not_a_crash() -> None:
    pipeline = TesseractExtractionPipeline()
    with pytest.raises(ExtractionError):
        pipeline.extract(data=b"not actually a pdf", content_type="application/pdf")


def test_ocr_pil_image_filters_empty_words_and_averages_confidence() -> None:
    fake_ocr_output = {
        "text": ["", "Hello", "  ", "world", "!"],
        "conf": ["-1", "95", "-1", "85", "90"],
    }
    with patch(
        "modules.ocr.tesseract.pytesseract.image_to_data",
        return_value=fake_ocr_output,
    ):
        text, confidence = _ocr_pil_image(Image.new("RGB", (10, 10)))

    assert text == "Hello world !"
    assert confidence == pytest.approx((95 + 85 + 90) / 3 / 100.0)


def test_ocr_pil_image_handles_no_recognized_words() -> None:
    fake_ocr_output = {"text": ["", "  "], "conf": ["-1", "-1"]}
    with patch(
        "modules.ocr.tesseract.pytesseract.image_to_data",
        return_value=fake_ocr_output,
    ):
        text, confidence = _ocr_pil_image(Image.new("RGB", (10, 10)))

    assert text == ""
    assert confidence == 0.0


def _tiny_pdf_bytes(page_count: int) -> bytes:
    doc = pdfium.PdfDocument.new()
    for _ in range(page_count):
        doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def test_ocr_pdf_joins_nonblank_pages_and_skips_blank_ones() -> None:
    pdf_bytes = _tiny_pdf_bytes(page_count=2)
    responses = iter(
        [
            {"text": ["Page", "one"], "conf": ["90", "80"]},
            {"text": ["", ""], "conf": ["-1", "-1"]},
        ]
    )

    with patch(
        "modules.ocr.tesseract.pytesseract.image_to_data",
        side_effect=lambda *args, **kwargs: next(responses),
    ):
        raw_text, confidence = TesseractExtractionPipeline._ocr_pdf(
            pdf_bytes, max_pages=_UNLIMITED_PAGES
        )

    assert raw_text == "Page one"
    assert confidence == pytest.approx((90 + 80) / 2 / 100.0)


def test_ocr_pdf_with_no_text_anywhere_returns_zero_confidence() -> None:
    pdf_bytes = _tiny_pdf_bytes(page_count=1)

    with patch(
        "modules.ocr.tesseract.pytesseract.image_to_data",
        return_value={"text": [""], "conf": ["-1"]},
    ):
        raw_text, confidence = TesseractExtractionPipeline._ocr_pdf(
            pdf_bytes, max_pages=_UNLIMITED_PAGES
        )

    assert raw_text == ""
    assert confidence == 0.0


def test_ocr_pdf_rejects_documents_exceeding_page_limit() -> None:
    pdf_bytes = _tiny_pdf_bytes(page_count=5)
    with pytest.raises(ExtractionError, match="5 pages"):
        TesseractExtractionPipeline._ocr_pdf(pdf_bytes, max_pages=4)


def test_ocr_pdf_accepts_documents_exactly_at_page_limit() -> None:
    pdf_bytes = _tiny_pdf_bytes(page_count=3)
    with patch(
        "modules.ocr.tesseract.pytesseract.image_to_data",
        return_value={"text": ["ok"], "conf": ["90"]},
    ):
        raw_text, _confidence = TesseractExtractionPipeline._ocr_pdf(pdf_bytes, max_pages=3)
    assert raw_text  # didn't raise, actually processed the pages


def test_decompression_bomb_raises_extraction_error_not_a_crash() -> None:
    # Simulates an "image bomb" by lowering Pillow's pixel budget rather
    # than generating a genuinely huge real file — DecompressionBombError
    # isn't a subclass of UnidentifiedImageError, so this is a distinct
    # code path from test_corrupted_image_raises_extraction_error_not_a_crash.
    original_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 100
    try:
        img = Image.new("RGB", (50, 50), color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        pipeline = TesseractExtractionPipeline()
        with pytest.raises(ExtractionError):
            pipeline.extract(data=buf.getvalue(), content_type="image/png")
    finally:
        Image.MAX_IMAGE_PIXELS = original_limit
