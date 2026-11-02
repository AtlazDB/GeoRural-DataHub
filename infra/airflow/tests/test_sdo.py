"""Conversão shapely → SDO_GEOMETRY (a parte que não precisa do banco)."""

import sys
from pathlib import Path

import pytest
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dags"))

from georural import sdo  # noqa: E402

QUADRADO = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
COM_BURACO = Polygon(
    [(0, 0), (4, 0), (4, 4), (0, 4)],
    holes=[[(1, 1), (1, 2), (2, 2), (2, 1)]],
)


def test_poligono_simples():
    gtype, elem, ords = sdo.elementos(QUADRADO)
    assert gtype == 2003
    assert elem == [1, 1003, 1]
    assert ords == [0, 0, 1, 0, 1, 1, 0, 1, 0, 0]  # o anel fecha repetindo o primeiro ponto


def test_poligono_com_buraco():
    gtype, elem, ords = sdo.elementos(COM_BURACO)
    assert gtype == 2003
    # anel externo começa na posição 1; o buraco começa depois dos 5 pontos (10 valores)
    assert elem == [1, 1003, 1, 11, 2003, 1]
    assert len(ords) == 20


def test_multipoligono():
    gtype, elem, _ = sdo.elementos(MultiPolygon([QUADRADO, COM_BURACO]))
    assert gtype == 2007
    assert elem == [1, 1003, 1, 11, 1003, 1, 21, 2003, 1]


def test_recusa_o_que_nao_tem_conversao():
    with pytest.raises(ValueError):
        sdo.elementos(GeometryCollection([Point(-51.0, -25.0)]))