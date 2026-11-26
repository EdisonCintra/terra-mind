"""Geometria do talhão (contrato 18): valida o GeoJSON do mapa e calcula o centro e a área.

O mapa do painel manda um `Point` ou um `Polygon` em WGS84 (`[lon, lat]`). O servidor não confia no
navegador: confere a forma, calcula o centro (vai para `erp.talhoes.lat` e `lon`, que o Monitor usa) e a área
em hectares (sugestão para `area_ha`). A área usa uma projeção local (equiretangular em torno do centro), com
erro desprezível para talhões de algumas centenas de hectares.
"""

import math
from typing import Any

RAIO_TERRA_M = 6_371_008.8
MAX_VERTICES = 500


class GeometriaInvalida(ValueError):
    """A mensagem é para a tela."""


def _par(ponto: Any) -> tuple[float, float]:
    if (
        not isinstance(ponto, (list, tuple))
        or len(ponto) < 2
        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in ponto[:2])
    ):
        raise GeometriaInvalida("Coordenada inválida na geometria.")
    lon, lat = float(ponto[0]), float(ponto[1])
    if not (math.isfinite(lon) and math.isfinite(lat)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise GeometriaInvalida("Coordenada fora do mundo (longitude de -180 a 180, latitude de -90 a 90).")
    return lon, lat


def validar(geom: Any) -> dict[str, Any]:
    """A geometria normalizada (só `type` e `coordinates`, anel fechado) ou `GeometriaInvalida`."""
    if not isinstance(geom, dict) or geom.get("type") not in ("Point", "Polygon"):
        raise GeometriaInvalida("Desenhe o talhão no mapa: um ponto ou um polígono.")
    coords = geom.get("coordinates")
    if geom["type"] == "Point":
        lon, lat = _par(coords)
        return {"type": "Point", "coordinates": [lon, lat]}
    if not isinstance(coords, list) or not coords or not isinstance(coords[0], list):
        raise GeometriaInvalida("Polígono sem contorno.")
    anel = [_par(p) for p in coords[0]]
    if len(anel) > MAX_VERTICES:
        raise GeometriaInvalida(f"Polígono com vértices demais (máximo {MAX_VERTICES}).")
    if len(anel) >= 2 and anel[0] == anel[-1]:
        anel = anel[:-1]
    if len(anel) < 3:
        raise GeometriaInvalida("O polígono precisa de pelo menos 3 vértices.")
    if len(set(anel)) < 3:
        raise GeometriaInvalida("O polígono tem vértices repetidos demais.")
    anel.append(anel[0])  # GeoJSON: anel fechado
    normal = {"type": "Polygon", "coordinates": [[[lon, lat] for lon, lat in anel]]}
    if area_ha(normal) <= 0:
        raise GeometriaInvalida("O polígono não tem área.")
    return normal


def _metros(anel: list[tuple[float, float]], lat0: float, lon0: float) -> list[tuple[float, float]]:
    k = math.cos(math.radians(lat0))
    return [
        (math.radians(lon - lon0) * RAIO_TERRA_M * k, math.radians(lat - lat0) * RAIO_TERRA_M)
        for lon, lat in anel
    ]


def _anel(geom: dict[str, Any]) -> list[tuple[float, float]]:
    pontos = [_par(p) for p in geom["coordinates"][0]]
    return pontos[:-1] if pontos[0] == pontos[-1] else pontos


def area_ha(geom: dict[str, Any]) -> float:
    """Área em hectares; 0 para ponto."""
    if geom["type"] != "Polygon":
        return 0.0
    anel = _anel(geom)
    lat0 = sum(lat for _, lat in anel) / len(anel)
    lon0 = sum(lon for lon, _ in anel) / len(anel)
    xy = _metros(anel, lat0, lon0)
    dobro = sum(
        xy[i][0] * xy[(i + 1) % len(xy)][1] - xy[(i + 1) % len(xy)][0] * xy[i][1] for i in range(len(xy))
    )
    return abs(dobro) / 2 / 10_000


def centro(geom: dict[str, Any]) -> tuple[float, float]:
    """`(lat, lon)` do centro: o próprio ponto, ou o centroide do polígono."""
    if geom["type"] == "Point":
        lon, lat = _par(geom["coordinates"])
        return lat, lon
    anel = _anel(geom)
    lat0 = sum(lat for _, lat in anel) / len(anel)
    lon0 = sum(lon for lon, _ in anel) / len(anel)
    xy = _metros(anel, lat0, lon0)
    dobro = sum(
        xy[i][0] * xy[(i + 1) % len(xy)][1] - xy[(i + 1) % len(xy)][0] * xy[i][1] for i in range(len(xy))
    )
    if abs(dobro) < 1e-9:  # degenerado: média dos vértices
        return lat0, lon0
    cx = sum(
        (xy[i][0] + xy[(i + 1) % len(xy)][0])
        * (xy[i][0] * xy[(i + 1) % len(xy)][1] - xy[(i + 1) % len(xy)][0] * xy[i][1])
        for i in range(len(xy))
    ) / (3 * dobro)
    cy = sum(
        (xy[i][1] + xy[(i + 1) % len(xy)][1])
        * (xy[i][0] * xy[(i + 1) % len(xy)][1] - xy[(i + 1) % len(xy)][0] * xy[i][1])
        for i in range(len(xy))
    ) / (3 * dobro)
    k = math.cos(math.radians(lat0))
    return lat0 + math.degrees(cy / RAIO_TERRA_M), lon0 + math.degrees(cx / (RAIO_TERRA_M * k))
