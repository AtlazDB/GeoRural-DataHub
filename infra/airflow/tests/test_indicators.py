"""As contas do IRL e do IFC (indicators.py), sem banco: os casos vêm do IndicadorCalculoTest do
back, que conferia o pkg_indicadores. O Python tem que dar os mesmos números."""

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dags"))

from georural import indicators  # noqa: E402


@pytest.mark.parametrize("value, expected", [
    ("2.00005", "2.0001"),     # o 0,5 sobe (o round() do Python iria para o par)
    ("2.00015", "2.0002"),
    ("-2.00005", "-2.0001"),   # negativo: para longe do zero, como o saldo do IRL
    ("15.42285", "15.4229"),
    ("15.42284", "15.4228"),
    ("100", "100.0000"),
])
def test_round4_is_oracle_round(value, expected):
    assert indicators.round4(Decimal(value)) == Decimal(expected)


# ---- IRL (casos do IndicadorCalculoTest do back)

def test_irl_matches_the_hand_calculation():
    # 15,5 / 100,5 x 100 = 15,4229% -> abaixo de 20%. Saldo: 15,5 - 20,1 = -4,6 ha
    irl = indicators.calculate_irl(Decimal("100.5"), Decimal("15.5"), Decimal("20"))

    assert irl.value == Decimal("15.4229")
    assert irl.status == indicators.CRITICO
    assert irl.legal_reserve_ha == Decimal("15.5")
    assert irl.balance_ha == Decimal("-4.6")


def test_irl_without_legal_reserve_is_zero():
    irl = indicators.calculate_irl(Decimal("50"), None, Decimal("20"))

    assert irl.value == 0
    assert irl.status == indicators.CRITICO
    assert irl.balance_ha == Decimal("-10")  # 0 - 20% de 50 ha


def test_irl_reserve_bigger_than_property_counts_as_100():
    irl = indicators.calculate_irl(Decimal("10"), Decimal("15"), Decimal("20"))

    assert irl.value == 100
    assert irl.legal_reserve_ha == Decimal("10")
    assert irl.status == indicators.CONFORME


def test_irl_exactly_at_the_minimum_is_conforme():
    irl = indicators.calculate_irl(Decimal("100"), Decimal("20"), Decimal("20"))

    assert irl.status == indicators.CONFORME


def test_irl_status_compares_the_rounded_value():
    # 19,99995% arredonda para 20,0000%: no PL/SQL dá CONFORME
    irl = indicators.calculate_irl(Decimal("100000"), Decimal("19999.95"), Decimal("20"))

    assert irl.value == Decimal("20.0000")
    assert irl.status == indicators.CONFORME


# ---- IFC

def test_ifc_matches_the_hand_calculation():
    # 2 / 100,5 x 1000 = 19,9005 -> acima de zero e abaixo de 20: Alerta
    ifc = indicators.calculate_ifc(Decimal("100.5"), 2, Decimal("1000"), Decimal("20"))

    assert ifc.value == Decimal("19.9005")
    assert ifc.status == indicators.ALERTA
    assert ifc.fires == 2


def test_ifc_without_fires_is_conforme():
    ifc = indicators.calculate_ifc(Decimal("50"), 0, Decimal("1000"), Decimal("20"))

    assert ifc.value == 0
    assert ifc.status == indicators.CONFORME


def test_ifc_at_the_critical_limit_is_critico():
    # 2 / 100 x 1000 = 20 -> igual ao limite: Crítico
    ifc = indicators.calculate_ifc(Decimal("100"), 2, Decimal("1000"), Decimal("20"))

    assert ifc.value == 20
    assert ifc.status == indicators.CRITICO


# ---- janela do IFC (ADD_MONTHS do Oracle)

@pytest.mark.parametrize("end, months, expected", [
    (datetime(2026, 5, 15, 10, 30, 15, 500000), 12, datetime(2025, 5, 15, 10, 30, 15)),  # sem fração
    (datetime(2026, 4, 30, 8, 0), 1, datetime(2026, 3, 31, 8, 0)),   # último dia -> último dia
    (datetime(2026, 3, 31, 8, 0), 1, datetime(2026, 2, 28, 8, 0)),   # dia 31 não existe em fevereiro
    (datetime(2024, 2, 29, 8, 0), 12, datetime(2023, 2, 28, 8, 0)),  # ano bissexto
    (datetime(2026, 1, 10, 8, 0), 12, datetime(2025, 1, 10, 8, 0)),  # vira o ano
])
def test_window_start_is_oracle_add_months(end, months, expected):
    assert indicators.window_start(end, months) == expected


# ---- focos por imóvel (GeoPandas)

def _properties():
    # A e B são vizinhos: dividem a linha x = 1. C fica longe.
    return gpd.GeoDataFrame(
        {"cod_imovel": ["A", "B", "C"]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1), box(10, 10, 11, 11)],
        crs="EPSG:4326",
    )


def _fires(*points):
    return gpd.GeoDataFrame(geometry=[Point(x, y) for x, y in points], crs="EPSG:4326")


def test_count_fires_inside_border_and_outside():
    fires = _fires((0.5, 0.5), (0, 0.5), (5, 5))  # dentro de A, na borda de A, fora de todos

    counts = indicators.count_fires(_properties(), fires)

    assert counts["A"] == 2  # a borda conta
    assert counts["C"] == 0


def test_count_fires_on_the_shared_border_counts_for_both():
    counts = indicators.count_fires(_properties(), _fires((1, 0.5)))

    assert counts["A"] == 1
    assert counts["B"] == 1


def test_count_fires_keeps_every_property_even_without_fires():
    counts = indicators.count_fires(_properties(), _fires())

    assert list(counts.index) == ["A", "B", "C"]
    assert (counts == 0).all()


# ---- indicadores cadastrados: faixas e densidade de pontos (US13.T06)

RANGES = [
    {"status": "CONFORME", "limit": 5, "includeLimit": True},
    {"status": "ALERTA", "limit": 20, "includeLimit": False},
    {"status": "CRITICO", "limit": None, "includeLimit": None},
]


@pytest.mark.parametrize("value, expected", [
    ("0", "CONFORME"),
    ("5", "CONFORME"),        # includeLimit: o 5 fica nesta faixa
    ("5.0001", "ALERTA"),
    ("19.9999", "ALERTA"),
    ("20", "CRITICO"),        # sem includeLimit: o 20 vai para a próxima
    ("1000", "CRITICO"),
])
def test_status_from_ranges(value, expected):
    assert indicators.status_from_ranges(Decimal(value), RANGES) == expected


def test_status_from_ranges_with_a_single_range():
    assert indicators.status_from_ranges(Decimal("42"), [{"status": "VALIDACAO"}]) == "VALIDACAO"


def test_status_from_ranges_with_decimal_limit_from_json():
    # o JSON pode trazer 10.5 como float: a comparação tem que ser exata
    ranges = [{"status": "CONFORME", "limit": 10.5, "includeLimit": True}, {"status": "CRITICO"}]

    assert indicators.status_from_ranges(Decimal("10.5"), ranges) == "CONFORME"
    assert indicators.status_from_ranges(Decimal("10.5001"), ranges) == "CRITICO"


def test_point_density():
    # 2 / 100,5 x 1000 = 19,9005 -> abaixo de 20: ALERTA
    result = indicators.calculate_point_density(Decimal("100.5"), 2, Decimal("1000"), RANGES)

    assert result.value == Decimal("19.9005")
    assert result.status == "ALERTA"
    assert result.points == 2


# ---- cobertura de área (US13.T07)

def _imovel(*caixas):
    return gpd.GeoDataFrame({"cod_imovel": [c for c, _ in caixas]}, geometry=[g for _, g in caixas], crs="EPSG:4326")


def _poligonos(*geometrias):
    return gpd.GeoDataFrame(geometry=list(geometrias), crs="EPSG:4326")


def _cobertura(imoveis, feicoes):
    tabela = indicators.coverage_by_property(imoveis, feicoes)
    return {c.cod_imovel: c for c in tabela.itertuples(index=False)}


def test_coverage_of_half_the_property_is_50_percent():
    # metade oeste do quadrado: a área geodésica de um retângulo lat/lon é proporcional à largura
    cob = _cobertura(_imovel(("A", box(0, 0, 0.1, 0.1))), _poligonos(box(0, 0, 0.05, 0.1)))["A"]

    result = indicators.calculate_area_coverage(cob.property_ha, cob.covered_ha, RANGES)

    assert abs(result.value - Decimal("50")) <= Decimal("0.0001")
    assert result.status == "CRITICO"   # 50 > 20: a última faixa


def test_overlapping_features_do_not_count_twice():
    duas = _poligonos(box(0, 0, 0.05, 0.1), box(0, 0, 0.05, 0.1))   # a mesma metade, duas vezes
    uma = _poligonos(box(0, 0, 0.05, 0.1))
    imovel = _imovel(("A", box(0, 0, 0.1, 0.1)))

    assert _cobertura(imovel, duas)["A"].covered_ha == _cobertura(imovel, uma)["A"].covered_ha


def test_feature_bigger_than_the_property_covers_100_percent():
    cob = _cobertura(_imovel(("A", box(0, 0, 0.1, 0.1))), _poligonos(box(-1, -1, 1, 1)))["A"]

    assert cob.covered_ha == cob.property_ha
    assert indicators.calculate_area_coverage(cob.property_ha, cob.covered_ha, RANGES).value == Decimal("100.0000")


def test_feature_that_only_touches_the_border_covers_nothing():
    # encosta na borda leste: a interseção é uma linha, sem área
    cob = _cobertura(_imovel(("A", box(0, 0, 0.1, 0.1))), _poligonos(box(0.1, 0, 0.2, 0.1)))["A"]

    assert cob.covered_ha == 0


def test_property_without_features_does_not_appear():
    tabela = indicators.coverage_by_property(_imovel(("A", box(0, 0, 0.1, 0.1)), ("B", box(5, 5, 5.1, 5.1))),
                                             _poligonos(box(0, 0, 0.05, 0.1)))

    assert list(tabela["cod_imovel"]) == ["A"]


def test_invalid_feature_is_fixed_before_the_intersection():
    from shapely.geometry import Polygon
    gravata = Polygon([(0, 0), (0.1, 0.1), (0.1, 0), (0, 0.1), (0, 0)])   # anel que se cruza

    cob = _cobertura(_imovel(("A", box(0, 0, 0.1, 0.1))), _poligonos(gravata))["A"]

    assert 0 < cob.covered_ha < cob.property_ha


def test_area_ha_is_geodesic_and_in_hectares():
    # 0,01° x 0,01° no equador: ~1,11 km x 1,11 km = ~123 ha
    assert Decimal("122") < indicators.area_ha(box(0, 0, 0.01, 0.01)) < Decimal("124")
