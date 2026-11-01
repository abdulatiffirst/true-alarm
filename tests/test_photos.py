import hashlib
from io import BytesIO

import pytest
from PIL import Image

from app.photos import PhotoRejected, process_photo
from tests.helpers import GPS_IFD, make_jpeg

LIMITS = dict(max_bytes=10_000_000, max_pixels=40_000_000, max_side=1600, thumb_side=400, quality=85)


def run(data, **override):
    return process_photo(data, **{**LIMITS, **override})


def png_bytes(size=(50, 50), mode="RGB", color=(10, 20, 30)):
    buffer = BytesIO()
    Image.new(mode, size, color).save(buffer, "PNG")
    return buffer.getvalue()


def test_fixture_really_contains_exif_and_gps():
    original = Image.open(BytesIO(make_jpeg()))
    assert original.getexif().get(0x010F) == "SecretMaker"
    assert original.getexif().get_ifd(GPS_IFD)


def test_all_metadata_is_removed_from_both_files():
    result = run(make_jpeg())
    for blob in (result.full, result.thumb):
        img = Image.open(BytesIO(blob))
        assert img.format == "JPEG"
        assert len(img.getexif()) == 0
        assert not img.getexif().get_ifd(GPS_IFD)
        assert b"Exif" not in blob
        assert b"SecretMaker" not in blob and b"SecretSoftware" not in blob


def test_fingerprint_is_of_original_bytes_not_processed():
    original = make_jpeg()
    result = run(original)
    assert result.sha256 == hashlib.sha256(original).hexdigest()
    assert result.sha256 != hashlib.sha256(result.full).hexdigest()


def test_exif_orientation_is_applied_before_metadata_is_dropped():
    # Хранится 200x100 с пометкой «повернуть» (6): после обработки фото стоит вертикально.
    result = run(make_jpeg(size=(200, 100), orientation=6))
    assert (result.width, result.height) == (100, 200)
    assert Image.open(BytesIO(result.full)).size == (100, 200)


def test_large_image_is_downscaled_and_thumbnail_is_smaller():
    result = run(make_jpeg(size=(3200, 2400), with_exif=False))
    assert max(Image.open(BytesIO(result.full)).size) == 1600
    assert max(Image.open(BytesIO(result.thumb)).size) == 400
    assert len(result.thumb) < len(result.full)


def test_small_image_is_not_upscaled():
    result = run(make_jpeg(size=(100, 50), with_exif=False))
    assert Image.open(BytesIO(result.full)).size == (100, 50)


def test_png_with_transparency_becomes_jpeg():
    result = run(png_bytes(mode="RGBA", color=(255, 0, 0, 0)))
    assert Image.open(BytesIO(result.full)).format == "JPEG"


def test_empty_file_rejected():
    with pytest.raises(PhotoRejected) as exc:
        run(b"")
    assert exc.value.code == "empty"


def test_too_large_file_rejected():
    with pytest.raises(PhotoRejected) as exc:
        run(make_jpeg(), max_bytes=100)
    assert exc.value.code == "too_large"


@pytest.mark.parametrize("data", [b"hello world", b"<html><script>alert(1)</script></html>", b"\xff\xd8\xff garbage"])
def test_non_images_rejected(data):
    with pytest.raises(PhotoRejected) as exc:
        run(data)
    assert exc.value.code == "not_an_image"


def test_unsupported_real_format_rejected():
    buffer = BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, "GIF")
    with pytest.raises(PhotoRejected) as exc:
        run(buffer.getvalue())
    assert exc.value.code == "unsupported_type"


def test_pixel_bomb_rejected_before_decoding():
    # Огромная картинка, которая в сжатом виде занимает почти ничего.
    buffer = BytesIO()
    Image.new("1", (20_000, 20_000)).save(buffer, "PNG")
    assert len(buffer.getvalue()) < 1_000_000
    with pytest.raises(PhotoRejected) as exc:
        run(buffer.getvalue())
    assert exc.value.code in {"too_many_pixels", "not_an_image"}


def test_user_messages_are_in_russian_and_nonempty():
    with pytest.raises(PhotoRejected) as exc:
        run(b"hello")
    assert exc.value.user_message and any("а" <= ch <= "я" for ch in exc.value.user_message.lower())
