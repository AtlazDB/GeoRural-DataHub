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
