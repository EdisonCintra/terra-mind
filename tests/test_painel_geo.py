"""Geometria do talhão (contrato 18): validação, centro e área."""

import math

import pytest

from painel import geo
from painel.geo import GeometriaInvalida

LAT, LON = -17.79, -50.92
M_LAT = 111_194.9  # metros por grau de latitude (esfera de 6.371 km)


def quadrado(lado_m: float, lat: float = LAT, lon: float = LON) -> dict:
    dlat = lado_m / 2 / M_LAT
    dlon = lado_m / 2 / (M_LAT * math.cos(math.radians(lat)))
    anel = [
        [lon - dlon, lat - dlat],
        [lon + dlon, lat - dlat],
        [lon + dlon, lat + dlat],
        [lon - dlon, lat + dlat],
    ]
    return {"type": "Polygon", "coordinates": [anel]}


def test_area_de_um_quadrado_de_1_km_e_100_hectares():
    assert geo.area_ha(geo.validar(quadrado(1000))) == pytest.approx(100, rel=0.01)
    assert geo.area_ha(geo.validar(quadrado(300))) == pytest.approx(9, rel=0.01)


def test_centro_do_poligono_e_do_ponto():
    lat, lon = geo.centro(geo.validar(quadrado(1000)))
    assert lat == pytest.approx(LAT, abs=1e-6) and lon == pytest.approx(LON, abs=1e-6)
    assert geo.centro({"type": "Point", "coordinates": [LON, LAT]}) == (LAT, LON)


def test_centro_de_um_triangulo_e_o_centroide_e_nao_a_media_dos_vertices_de_um_retangulo_torto():
    # retângulo com um vértice extra no meio de um lado: o centroide fica no meio, a média dos vértices não
    anel = [[0, 0], [0.002, 0], [0.004, 0], [0.004, 0.002], [0, 0.002]]
    lat, lon = geo.centro({"type": "Polygon", "coordinates": [anel + [anel[0]]]})
    assert lon == pytest.approx(0.002, abs=1e-6) and lat == pytest.approx(0.001, abs=1e-6)


def test_poligono_vem_fechado_e_aceita_anel_ja_fechado():
    aberto = quadrado(500)
    fechado = {"type": "Polygon", "coordinates": [aberto["coordinates"][0] + [aberto["coordinates"][0][0]]]}
    a, b = geo.validar(aberto), geo.validar(fechado)
    assert a == b and a["coordinates"][0][0] == a["coordinates"][0][-1] and len(a["coordinates"][0]) == 5


def test_valida_so_type_e_coordinates():
    g = geo.validar({"type": "Point", "coordinates": [LON, LAT], "propriedade": "x", "crs": "y"})
    assert g == {"type": "Point", "coordinates": [LON, LAT]}


@pytest.mark.parametrize(
    "geom",
    [
        None,
        "texto",
        {},
        {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        {"type": "Point", "coordinates": [200, 0]},  # longitude fora
        {"type": "Point", "coordinates": [0, 91]},  # latitude fora
        {"type": "Point", "coordinates": ["a", "b"]},
        {"type": "Point", "coordinates": [True, False]},
        {"type": "Point", "coordinates": [float("nan"), 0]},
        {"type": "Polygon", "coordinates": []},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]},  # 2 vértices
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 1], [0, 0]]]},  # só 2 pontos distintos
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [2, 2]]]},  # colinear: sem área
    ],
)
def test_geometria_invalida_e_recusada(geom):
    with pytest.raises(GeometriaInvalida):
        geo.validar(geom)


def test_poligono_com_vertices_demais_e_recusado():
    anel = [[i / 1e5, (i % 7) / 1e5] for i in range(geo.MAX_VERTICES + 5)]
    with pytest.raises(GeometriaInvalida, match="vértices demais"):
        geo.validar({"type": "Polygon", "coordinates": [anel]})


def test_ponto_nao_tem_area():
    assert geo.area_ha({"type": "Point", "coordinates": [LON, LAT]}) == 0
