"""Вспомогательные средства для тестов: картинки и «поддельный» Supabase Storage."""
import json
from io import BytesIO

import httpx
from PIL import Image

GPS_IFD = 0x8825


def make_jpeg(color=(200, 30, 30), size=(800, 600), *, with_exif=True, orientation=1) -> bytes:
    """JPEG с «секретными» метаданными (камера, программа, GPS), чтобы проверять их удаление."""
    img = Image.new("RGB", size, color)
    kwargs = {}
    if with_exif:
        exif = Image.Exif()
        exif[0x010F] = "SecretMaker"
        exif[0x0110] = "SecretModel"
        exif[0x0112] = orientation
        exif[0x0131] = "SecretSoftware"
        gps = exif.get_ifd(GPS_IFD)
        gps[1] = "N"
        gps[2] = (41.0, 18.0, 0.0)
        gps[3] = "E"
        gps[4] = (69.0, 14.0, 0.0)
        kwargs["exif"] = exif
    buffer = BytesIO()
    img.save(buffer, "JPEG", **kwargs)
    return buffer.getvalue()


class FakeSupabase:
    """Хранилище в памяти, отвечающее как Supabase Storage (загрузка и массовое удаление)."""

    def __init__(self, bucket: str = "bkt") -> None:
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}
        self.delete_calls: list[list[str]] = []
        self.fail_deletes = False
        self.fail_upload_if = None  # функция path -> bool
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        prefix = f"/storage/v1/object/{self.bucket}"
        path = request.url.path
        if request.method == "POST" and path.startswith(prefix + "/"):
            name = path[len(prefix) + 1 :]
            if self.fail_upload_if and self.fail_upload_if(name):
                return httpx.Response(500, json={"error": "boom"})
            self.objects[name] = request.content
            return httpx.Response(200, json={"Key": f"{self.bucket}/{name}"})
        if request.method == "DELETE" and path == prefix:
            prefixes = json.loads(request.content)["prefixes"]
            self.delete_calls.append(list(prefixes))
            if self.fail_deletes:
                return httpx.Response(500, json={"error": "boom"})
            deleted = []
            for name in prefixes:
                if self.objects.pop(name, None) is not None:  # отсутствующие пропускаются
                    deleted.append({"name": name})
            return httpx.Response(200, json=deleted)
        return httpx.Response(404, json={"error": "not found"})
