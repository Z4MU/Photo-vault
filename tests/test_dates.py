"""
Tests de extracción de fecha desde nombres de archivo y carpetas.
Ver CLAUDE.md §6: los patrones son estrictos a propósito.
"""

import pytest

from indexer import _extract_date_from_string


@pytest.mark.parametrize(
    "text, expected",
    [
        # Separadores explícitos
        ("2019-07-04 fiesta.jpg", (2019, 7)),
        ("2019_07_04.png", (2019, 7)),
        ("foto 2021-1-9.jpg", (2021, 1)),
        # Compacto YYYYMMDD
        ("20200512_123456.jpg", (2020, 5)),
        ("IMG_20200512_123456.jpg", (2020, 5)),
        ("VID_20181231_235959.mp4", (2018, 12)),
        # WhatsApp
        ("IMG-20200512-WA0001.jpg", (2020, 5)),
        ("VID-20230101-WA0042.mp4", (2023, 1)),
        # Año-mes sin día
        ("vacaciones_2020-05.jpg", (2020, 5)),
        # Rutas de carpeta
        (r"D:\Fotos\2017-08-15 Boda", (2017, 8)),
    ],
)
def test_fechas_validas(text, expected):
    assert _extract_date_from_string(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "wallpaper_1920x1080.png",  # resolución
        "v1920.png",  # versión
        "19201080.png",  # resolución concatenada (año 1920 < 1990)
        "3840x2160_4k.jpg",
        "foto.jpg",
        "IMG_1234.JPG",  # contador de cámara
        "DSC00042.jpg",
        "1985-03-02 viejo.jpg",  # año fuera de rango
    ],
)
def test_falsos_positivos(text):
    assert _extract_date_from_string(text) == (None, None)


def test_mes_invalido_guarda_solo_anio():
    assert _extract_date_from_string("captura_2021_13_01.png") == (2021, None)
