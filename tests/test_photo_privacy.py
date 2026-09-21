"""The data export tells people photo GPS is stripped. This proves it.

`not_collected` in the export includes "location (photo GPS data is stripped
before storage)". That is a privacy claim made to a parent about a child's
photos, and until now nothing tested it — the behaviour lived in a constant
(`_JPEG_DROP_MARKERS`) that a refactor could narrow without anything noticing.

A claim with no test behind it is a claim we cannot substantiate.
"""
import struct

import pytest

from app import PhotoRejected, sanitize_jpeg

# A recognizable needle. If sanitisation ever stops dropping APP segments, this
# string survives into storage and the test fails loudly.
GPS_NEEDLE = b"GPSLatitudeRef=N;GPSLatitude=51/1,30/1,26/1"


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def _jpeg(*, width=64, height=48, extras=b"") -> bytes:
    """A minimal but structurally valid JPEG, with whatever extras requested."""
    sof = struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + extras + _segment(0xC0, sof) + b"\xff\xd9"


def test_exif_gps_does_not_survive_sanitisation():
    exif = _segment(0xE1, b"Exif\x00\x00" + GPS_NEEDLE)
    raw = _jpeg(extras=exif)
    assert GPS_NEEDLE in raw, "the fixture must actually contain the GPS data"

    clean, width, height = sanitize_jpeg(raw)

    assert GPS_NEEDLE not in clean, (
        "GPS data survived sanitisation — the export's 'photo GPS data is "
        "stripped before storage' claim would be false")
    assert (width, height) == (64, 48), "dimensions must still be read correctly"


@pytest.mark.parametrize("marker,label", [
    (0xE0, "APP0/JFIF"), (0xE1, "APP1/EXIF"), (0xE2, "APP2"),
    (0xED, "APP13/Photoshop-IPTC"), (0xEE, "APP14/Adobe"), (0xFE, "COM comment"),
])
def test_every_metadata_segment_is_dropped(marker, label):
    """Not just EXIF: a device name in a COM comment is also identifying."""
    needle = b"SECRET-" + label.encode() + b"-PAYLOAD"
    clean, _w, _h = sanitize_jpeg(_jpeg(extras=_segment(marker, needle)))
    assert needle not in clean, f"{label} payload survived sanitisation"


def test_pixel_data_is_preserved():
    """Stripping metadata must not mean stripping the photo."""
    scan = b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00"
    pixels = b"\x12\x34\x56\x78" * 8
    raw = b"\xff\xd8" + _segment(0xC0, struct.pack(">BHHB", 8, 48, 64, 1)
                                 + b"\x01\x11\x00") + scan + pixels + b"\xff\xd9"
    clean, _w, _h = sanitize_jpeg(raw)
    assert pixels in clean, "sanitisation destroyed the image data"


def test_a_file_that_merely_starts_like_a_jpeg_is_rejected():
    with pytest.raises(PhotoRejected):
        sanitize_jpeg(b"\xff\xd8" + b"not actually a jpeg at all")


def test_sanitisation_is_idempotent():
    """Re-sanitising stored bytes must not corrupt them."""
    once, _w, _h = sanitize_jpeg(_jpeg(extras=_segment(0xE1, b"Exif\x00\x00" + GPS_NEEDLE)))
    twice, _w2, _h2 = sanitize_jpeg(once)
    assert once == twice
