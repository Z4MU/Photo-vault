"""Fase 5: consultas de la galería continua, saltar a fecha, línea de tiempo, árbol de carpetas, EXIF."""

import pytest
from PIL import Image

import database as db
import indexer
import services
from models import SortField, SortOrder

DATES = [(2021, 3), (2021, 3), (2021, None), (2020, 12), (2020, 1), (None, None), (2019, 7), (None, None)]


@pytest.fixture
def photos(db_path):
    db.init_db()
    ids = []
    for i, (y, m) in enumerate(DATES):
        ids.append(db.upsert_photo(rf"G:\Fotos\{y}\img{i}.jpg", f"img{i}.jpg", y, m, 100 + i))
    return ids


@pytest.mark.parametrize("order", list(SortOrder))
def test_fila_de_una_fecha_coincide_con_la_galeria(photos, order):
    q = services.GalleryQuery(sort_order=order)
    gallery = services.get_gallery_chunk(q, 0, 100)

    def first(match) -> int:
        return next(i for i, p in enumerate(gallery) if match(p))

    for year, month in {(y, m) for y, m in DATES}:
        if year is None:
            assert services.gallery_row_of_date(q, None) == first(lambda p: p.year is None)
            continue
        # Año completo: la primera foto de ese año en el orden actual
        assert services.gallery_row_of_date(q, year) == first(lambda p, y=year: p.year == y)
        if month is None:
            target = first(lambda p, y=year: p.year == y and p.month is None)
            assert services.gallery_row_of_date(q, year, services.NO_MONTH) == target
        else:
            target = first(lambda p, y=year, m=month: p.year == y and p.month == m)
            assert services.gallery_row_of_date(q, year, month) == target, (year, month, order)


def test_saltar_a_fecha_respeta_filtros(photos):
    tag = services.add_tag(photos[3], "x")  # 2020-12
    services.add_tag(photos[6], "x")  # 2019-07
    q = services.GalleryQuery(tag_ids=(tag.id,))
    assert services.gallery_row_of_date(q, 2020, 12) == 0
    assert services.gallery_row_of_date(q, 2019, 7) == 1


def test_saltar_a_fecha_exige_orden_por_fecha(photos):
    with pytest.raises(ValueError):
        services.gallery_row_of_date(services.GalleryQuery(sort_field=SortField.FILENAME), 2020, 1)


def test_ids_en_el_mismo_orden_que_la_galeria(photos):
    for field in SortField:
        q = services.GalleryQuery(sort_field=field, sort_order=SortOrder.ASC)
        assert services.get_gallery_ids(q) == [p.id for p in services.get_gallery_chunk(q, 0, 100)]
        assert services.get_gallery_ids(q, offset=2, limit=3) == [
            p.id for p in services.get_gallery_chunk(q, 2, 3)
        ]
    assert services.count_gallery(services.GalleryQuery()) == len(DATES)


def test_galeria_excluye_etiquetas_ocultas(photos):
    tag = services.add_tag(photos[0], "privado")
    services.set_tag_hidden(tag.id, True)
    q = services.GalleryQuery()
    assert services.count_gallery(q) == len(DATES) - 1
    assert photos[0] not in services.get_gallery_ids(q)


def test_linea_de_tiempo(photos):
    hist = services.get_date_histogram(services.GalleryQuery())
    assert [(b.year, b.month, b.count) for b in hist] == [
        (2021, 3, 2),
        (2021, None, 1),
        (2020, 12, 1),
        (2020, 1, 1),
        (2019, 7, 1),
        (None, None, 2),
    ]
    only_2020 = services.get_date_histogram(services.GalleryQuery(search="img4"))
    assert [(b.year, b.month) for b in only_2020] == [(2020, 1)]


def test_arbol_de_carpetas_suma_subcarpetas():
    roots = services.build_folder_tree(
        [
            ("G:\\", 1),
            (r"G:\Fotos", 2),
            (r"G:\Fotos\2020", 5),
            (r"G:\Fotos\2020\Playa", 3),
            (r"G:\Fotos\2021", 4),
            (r"D:\Otras", 7),
            (r"\\nas\fotos\viaje", 6),
        ]
    )
    by_name = {r.name: r for r in roots}
    g = by_name["G:"]
    assert (g.path, g.count, g.total) == ("G:\\", 1, 15)
    fotos = g.children[0]
    assert (fotos.name, fotos.count, fotos.total) == ("Fotos", 2, 14)
    assert [c.name for c in fotos.children] == ["2020", "2021"]
    assert fotos.children[0].total == 8 and fotos.children[0].children[0].path == r"G:\Fotos\2020\Playa"
    assert by_name["D:"].total == 7
    nas = next(r for r in roots if r.path.startswith("\\\\"))
    assert nas.total == 6 and nas.children[0].name == "viaje"


def test_arbol_de_carpetas_desde_la_db(photos):
    roots = services.get_folder_tree()
    assert len(roots) == 1 and roots[0].total == len(DATES)
    # Cada carpeta del árbol filtra exactamente sus fotos en la galería
    for node in roots[0].children[0].children:
        assert services.count_gallery(services.GalleryQuery(folder=node.path)) == node.total


def test_detalles_exif(tmp_path):
    p = tmp_path / "cam.jpg"
    exif = Image.Exif()
    exif[271] = "Apple"
    exif[272] = "iPhone 12"
    ifd = exif.get_ifd(0x8769)
    ifd[36867] = "2021:03:04 15:16:17"
    ifd[33434] = 1 / 125
    ifd[33437] = 1.6
    ifd[34855] = 64
    ifd[37386] = 4.2
    Image.new("RGB", (20, 20)).save(p, exif=exif)
    d = indexer.read_exif_details(str(p))
    assert d["Cámara"] == "Apple iPhone 12"
    assert d["Tomada"] == "04/03/2021 15:16"
    assert d["Ajustes"] == "1/125 s · f/1.6 · ISO 64 · 4.2 mm"


def test_detalles_exif_sin_exif_o_sin_archivo(tmp_path):
    p = tmp_path / "plano.png"
    Image.new("RGB", (5, 5)).save(p)
    assert indexer.read_exif_details(str(p)) == {}
    assert indexer.read_exif_details(str(tmp_path / "no-existe.jpg")) == {}
