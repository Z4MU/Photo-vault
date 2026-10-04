"""Caché de miniaturas: orientación EXIF (#4), tamaño en la clave (#6), purga."""

import io

from PIL import Image

import thumbnail_cache as tc


def _jpeg_with_orientation(path, w: int, h: int, orientation: int) -> None:
    img = Image.new("RGB", (w, h), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = orientation  # Orientation
    img.save(path, format="JPEG", exif=exif)


def _size_of(jpeg: bytes | None) -> tuple[int, int]:
    assert jpeg is not None, "no se generó la miniatura"
    with Image.open(io.BytesIO(jpeg)) as img:
        return img.size


def test_aplica_orientacion_exif(tmp_path):
    # Foto "acostada" 400x200 con orientación 6 (rotar 90°) → debe verse vertical
    src = tmp_path / "celular.jpg"
    _jpeg_with_orientation(src, 400, 200, orientation=6)
    w, h = _size_of(tc.get_thumbnail(str(src), size=100))
    assert h > w


def test_sin_orientacion_no_rota(tmp_path):
    src = tmp_path / "normal.jpg"
    Image.new("RGB", (400, 200)).save(src)
    w, h = _size_of(tc.get_thumbnail(str(src), size=100))
    assert (w, h) == (100, 50)


def test_tamanos_distintos_no_comparten_cache(tmp_path):
    src = tmp_path / "foto.jpg"
    Image.new("RGB", (1000, 800)).save(src)
    small = tc.get_thumbnail(str(src), size=200)
    big = tc.get_thumbnail(str(src), size=480)
    assert max(_size_of(small)) == 200
    assert max(_size_of(big)) == 480
    # Y desde caché siguen siendo distintas
    assert max(_size_of(tc.get_thumbnail(str(src), size=200))) == 200
    assert len(list(tc.CACHE_DIR.rglob("*.jpg"))) == 2


def test_cache_hit_no_regenera(tmp_path, monkeypatch):
    src = tmp_path / "foto.jpg"
    Image.new("RGB", (300, 300)).save(src)
    first = tc.get_thumbnail(str(src))

    def no_generar(*a, **k):
        raise AssertionError("no debía regenerar")

    monkeypatch.setattr(tc, "_generate", no_generar)
    assert tc.get_thumbnail(str(src)) == first


def test_purga_conserva_vigentes_y_borra_viejas(tmp_path):
    a = tmp_path / "a.jpg"
    Image.new("RGB", (50, 50)).save(a)
    b = tmp_path / "b.jpg"
    Image.new("RGB", (50, 50)).save(b)
    tc.get_thumbnail(str(a), size=200)
    tc.get_thumbnail(str(a), size=480)
    tc.get_thumbnail(str(b), size=200)
    # Miniatura con el formato de caché anterior (sin tamaño en el nombre)
    old = tc.CACHE_DIR / "ab" / ("ab" + "0" * 38 + ".jpg")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"x")

    removed = tc.purge_orphans([str(a)])  # b ya no está indexada

    assert removed == 2  # la de b + la vieja
    assert len(list(tc.CACHE_DIR.rglob("*.jpg"))) == 2


def test_videos_no_se_amontonan_en_una_carpeta(tmp_path):
    name = tc._cache_name("C:\\x\\video.mp4", 200, video=True)
    assert name.startswith("v_")
    assert tc._cache_path(name).parent.name != "v_"


def test_heic_sin_importar_indexer(tmp_path):
    """Regresión: la miniatura HEIC fallaba si indexer.py no se había importado antes."""
    import subprocess
    import sys
    from pathlib import Path

    pytest = __import__("pytest")
    pillow_heif = pytest.importorskip("pillow_heif")
    src = tmp_path / "foto.heic"
    pillow_heif.from_pillow(Image.new("RGB", (300, 200))).save(str(src))

    # Proceso limpio: solo se importa thumbnail_cache
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import thumbnail_cache as tc;"
        f"tc.CACHE_DIR = __import__('pathlib').Path(r'{tmp_path / 'cache'}');"
        f"print('OK' if tc.get_thumbnail(r'{src}', 100) else 'FALLA')"
    )
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-c", code, str(root)], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "OK", out.stderr


def test_archivo_ilegible_devuelve_none(tmp_path):
    bad = tmp_path / "roto.jpg"
    bad.write_bytes(b"no es una imagen")
    assert tc.get_thumbnail(str(bad)) is None
