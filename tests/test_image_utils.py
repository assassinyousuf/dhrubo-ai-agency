"""Tests for :mod:`dhrubo.tools.image_utils`."""

from __future__ import annotations

import base64

import pytest
from dhrubo.core.errors import ProviderError
from dhrubo.tools.image_utils import _PNG_1x1, detect_media_type, read_bytes, to_data_url


def test_detect_png(tmp_path) -> None:
    p = tmp_path / "a.png"
    p.write_bytes(_PNG_1x1)
    assert detect_media_type(p) == "image/png"


def test_detect_jpeg(tmp_path) -> None:
    p = tmp_path / "a.jpg"
    # Minimal valid JPEG header (SOI + APP0 marker).
    p.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00")
    assert detect_media_type(p) == "image/jpeg"


def test_detect_webp(tmp_path) -> None:
    p = tmp_path / "a.webp"
    # RIFF header (4) + 4 bytes size + 'WEBP' at offset 8.
    p.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
    assert detect_media_type(p) == "image/webp"


def test_detect_unknown_raises(tmp_path) -> None:
    p = tmp_path / "a.txt"
    p.write_bytes(b"hello world")
    with pytest.raises(ProviderError):
        detect_media_type(p)


def test_to_data_url_format(tmp_path) -> None:
    p = tmp_path / "a.png"
    p.write_bytes(_PNG_1x1)
    url = to_data_url(p)
    assert url.startswith("data:image/png;base64,")
    # Round-trip: the encoded payload should decode back to the original bytes.
    encoded = url.split(",", 1)[1]
    assert base64.b64decode(encoded) == _PNG_1x1


def test_to_data_url_explicit_media_type(tmp_path) -> None:
    p = tmp_path / "no_ext"
    p.write_bytes(_PNG_1x1)
    url = to_data_url(p, media_type="image/png")
    assert url.startswith("data:image/png;base64,")


def test_missing_file_raises(tmp_path) -> None:
    bogus = tmp_path / "does_not_exist.png"
    with pytest.raises(ProviderError):
        read_bytes(bogus)


def test_missing_file_detect_raises(tmp_path) -> None:
    bogus = tmp_path / "does_not_exist.png"
    with pytest.raises(ProviderError):
        detect_media_type(bogus)
