"""Проверка и подготовка фотографий перед публичным хранением.

Что делаем с принятым файлом:
1. считаем SHA-256 исходных байтов (до любых преобразований);
2. проверяем настоящий тип по содержимому (JPEG, PNG или WebP), размер и число пикселей;
3. применяем поворот из EXIF, затем создаём новое изображение без метаданных
   (EXIF, GPS, миниатюры, профили) и сохраняем его как JPEG;
4. делаем уменьшенное превью.
"""
import hashlib
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class PhotoRejected(Exception):
    """Фото не подходит. `user_message` можно показать человеку."""

    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(code)
        self.code = code
        self.user_message = user_message


@dataclass(frozen=True)
class ProcessedPhoto:
    sha256: str  # отпечаток ИСХОДНОГО принятого файла
    full: bytes  # очищенное фото (JPEG)
    thumb: bytes  # превью (JPEG)
    width: int
    height: int


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return img.convert("RGB")


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    img.save(buffer, "JPEG", quality=quality, optimize=True)  # метаданные не передаём
    return buffer.getvalue()


def process_photo(
    data: bytes,
    *,
    max_bytes: int,
    max_pixels: int,
    max_side: int,
    thumb_side: int,
    quality: int = 85,
) -> ProcessedPhoto:
    if not data:
        raise PhotoRejected("empty", "Файл пустой. Отправьте фотографию ещё раз.")
    if len(data) > max_bytes:
        raise PhotoRejected(
            "too_large", f"Фото слишком большое (максимум {max_bytes // 1_000_000} МБ)."
        )

    sha256 = hashlib.sha256(data).hexdigest()

    try:
        with Image.open(BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise PhotoRejected(
                    "unsupported_type", "Нужна обычная фотография (JPEG, PNG или WebP)."
                )
            if probe.width * probe.height > max_pixels:
                raise PhotoRejected(
                    "too_many_pixels", "У фотографии слишком большое разрешение. Сожмите её и отправьте снова."
                )
            probe.verify()

        with Image.open(BytesIO(data)) as img:
            img.load()  # для анимации берётся первый кадр
            oriented = ImageOps.exif_transpose(img)
            rgb = _to_rgb(oriented)
    except PhotoRejected:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise PhotoRejected(
            "not_an_image", "Не удалось прочитать файл как фотографию. Отправьте другое фото."
        ) from None

    # Новое изображение: в нём нет ни одного поля метаданных исходника.
    clean = Image.new("RGB", rgb.size)
    clean.paste(rgb)
    clean.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)  # без увеличения
    full = _encode_jpeg(clean, quality)

    thumb_img = clean.copy()
    thumb_img.thumbnail((thumb_side, thumb_side), Image.Resampling.LANCZOS)
    thumb = _encode_jpeg(thumb_img, quality)

    return ProcessedPhoto(sha256=sha256, full=full, thumb=thumb, width=clean.width, height=clean.height)
