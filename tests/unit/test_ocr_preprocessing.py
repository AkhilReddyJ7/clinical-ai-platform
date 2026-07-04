from unittest.mock import patch

import pytesseract
import pytest
from PIL import Image

from modules.ocr.tesseract import (
    _MIN_OCR_DIMENSION_PX,
    _correct_orientation,
    _normalize_for_ocr,
    _ocr_pil_image,
)


def test_normalize_converts_to_grayscale_and_autocontrasts() -> None:
    image = Image.new("RGB", (1200, 1200), color=(120, 130, 140))

    normalized = _normalize_for_ocr(image)

    assert normalized.mode == "L"


def test_normalize_upscales_small_low_resolution_images() -> None:
    tiny = Image.new("RGB", (200, 100), color="white")

    normalized = _normalize_for_ocr(tiny)

    # Short side (100) was below the threshold — both dimensions scale up
    # proportionally, so the short side now meets the minimum.
    assert min(normalized.size) >= _MIN_OCR_DIMENSION_PX
    assert normalized.width / normalized.height == pytest.approx(200 / 100)


def test_normalize_leaves_already_large_images_at_original_size() -> None:
    large = Image.new("RGB", (2000, 1500), color="white")

    normalized = _normalize_for_ocr(large)

    assert normalized.size == (2000, 1500)


def test_correct_orientation_rotates_when_osd_reports_rotation() -> None:
    image = Image.new("RGB", (300, 200), color="white")

    with patch(
        "modules.ocr.tesseract.pytesseract.image_to_osd",
        return_value={"rotate": 90},
    ):
        corrected = _correct_orientation(image)

    # A 90-degree correction on a non-square image swaps width and height.
    assert corrected.size == (200, 300)


def test_correct_orientation_is_a_noop_when_osd_reports_no_rotation() -> None:
    image = Image.new("RGB", (300, 200), color="white")

    with patch(
        "modules.ocr.tesseract.pytesseract.image_to_osd",
        return_value={"rotate": 0},
    ):
        corrected = _correct_orientation(image)

    assert corrected.size == (300, 200)


def test_correct_orientation_falls_back_gracefully_when_osd_fails() -> None:
    # Mirrors this project's real environment: no tesseract binary at all
    # raises TesseractNotFoundError; a sparse/blank image raises
    # TesseractError. Neither should propagate — this is a best-effort
    # enhancement, not a required step.
    image = Image.new("RGB", (300, 200), color="white")

    for exc in (
        pytesseract.TesseractNotFoundError(),
        pytesseract.TesseractError(1, "too few characters"),
    ):
        with patch("modules.ocr.tesseract.pytesseract.image_to_osd", side_effect=exc):
            corrected = _correct_orientation(image)
        assert corrected.size == (300, 200)


def test_ocr_pil_image_applies_preprocessing_by_default() -> None:
    fake_ocr_output = {"text": ["Hello"], "conf": ["90"]}
    tiny = Image.new("RGB", (50, 50), color="white")

    with (
        patch(
            "modules.ocr.tesseract.pytesseract.image_to_data",
            return_value=fake_ocr_output,
        ) as mock_image_to_data,
        patch(
            "modules.ocr.tesseract.pytesseract.image_to_osd",
            side_effect=pytesseract.TesseractNotFoundError(),
        ),
    ):
        text, confidence = _ocr_pil_image(tiny)

    assert text == "Hello"
    assert confidence == pytest.approx(0.9)
    # The image actually handed to Tesseract was preprocessed (grayscale,
    # upscaled) — a real behavioral difference from the raw input, not
    # just a call that happened to still be made.
    processed_image = mock_image_to_data.call_args.args[0]
    assert processed_image.mode == "L"
    assert min(processed_image.size) >= _MIN_OCR_DIMENSION_PX


def test_ocr_pil_image_skips_preprocessing_when_disabled() -> None:
    fake_ocr_output = {"text": ["Hello"], "conf": ["90"]}
    tiny = Image.new("RGB", (50, 50), color="white")

    with patch(
        "modules.ocr.tesseract.pytesseract.image_to_data",
        return_value=fake_ocr_output,
    ) as mock_image_to_data:
        _ocr_pil_image(tiny, preprocess=False)

    processed_image = mock_image_to_data.call_args.args[0]
    assert processed_image is tiny
    assert processed_image.mode == "RGB"
    assert processed_image.size == (50, 50)


def test_ocr_pil_image_passes_configured_psm_to_tesseract() -> None:
    fake_ocr_output = {"text": ["Hello"], "conf": ["90"]}

    with patch(
        "modules.ocr.tesseract.pytesseract.image_to_data",
        return_value=fake_ocr_output,
    ) as mock_image_to_data:
        _ocr_pil_image(Image.new("RGB", (1200, 1200)), preprocess=False, psm=6)

    assert mock_image_to_data.call_args.kwargs["config"] == "--psm 6"
