"""Indicadores cadastrados (custom_indicators.py), sem banco: um cursor falso responde às consultas
e guarda o que seria gravado."""

import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dags"))

from georural import custom_indicators  # noqa: E402
from georural.custom_indicators import IndicatorError  # noqa: E402

RANGES = [
    {"status": "CONFORME", "limit": 5, "includeLimit": True},
    {"status": "ALERTA", "limit": 20, "includeLimit": False},
    {"status": "CRITICO"},
]


class CursorFalso:
    """Devolve as linhas combinadas para cada consulta; guarda os executemany."""

    def __init__(self, respostas=None):
        self.respostas = respostas or {}
        self.outputtypehandler = None
        self.lotes = []
        self._linhas = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, **_binds):
        self._linhas = next((linhas for trecho, linhas in self.respostas.items() if trecho in sql), [])

    def fetchone(self):
        return self._linhas[0] if self._linhas else None

    def fetchall(self):
        return self._linhas

    def executemany(self, sql, linhas):
        self.lotes.append(list(linhas))


class ConexaoFalsa:
    def __init__(self):
        self.cursor_falso = CursorFalso()
        self.commits = 0

    def cursor(self):
        return self.cursor_falso

    def commit(self):
        self.commits += 1


def _properties():
    return pd.DataFrame({
        "cod_imovel": ["PR-1", "PR-2", "PR-3"],
        "area_ha": [Decimal("100.5"), Decimal("50"), Decimal("1000")],
        "versao_imovel_id": [7, 7, 7],
    })


# ---- montar as linhas

def test_one_row_per_property_with_value_and_status():
    counts = pd.Series({"PR-1": 2, "PR-3": 1})

    rows = custom_indicators.point_density_rows("DFOCO", _properties(), counts, 42, Decimal("1000"), RANGES)

    por_imovel = {r["cod_imovel"]: r for r in rows}
    assert por_imovel["PR-1"]["value"] == Decimal("19.9005")   # 2 / 100,5 x 1000
    assert por_imovel["PR-1"]["status"] == "ALERTA"
    assert por_imovel["PR-3"]["value"] == Decimal("1.0000")    # 1 / 1000 x 1000
    assert por_imovel["PR-3"]["status"] == "CONFORME"


def test_property_without_points_gets_zero():
    rows = custom_indicators.point_density_rows("DFOCO", _properties(), pd.Series(dtype=int), 42,
                                                Decimal("1000"), RANGES)

    assert len(rows) == 3
    assert all(r["value"] == 0 and r["status"] == "CONFORME" for r in rows)


def test_row_has_everything_the_merge_needs():
    rows = custom_indicators.point_density_rows("DFOCO", _properties(), pd.Series({"PR-2": 3}), 42,
                                                Decimal("1000"), RANGES)

    pr2 = next(r for r in rows if r["cod_imovel"] == "PR-2")
    assert pr2 == {
        "code": "DFOCO", "cod_imovel": "PR-2", "versao": 42, "versao_imovel": 7,
        "value": Decimal("60.0000"), "status": "CRITICO", "numerator": Decimal("3"), "area": Decimal("50"),
    }


# ---- ler o indicador

def _definition(active=1, model="POINT_DENSITY"):
    ranges = '[{"status":"CONFORME","limit":10.5,"includeLimit":true},{"status":"CRITICO","limit":null,"includeLimit":null}]'
    return CursorFalso({"indicator_definition": [(3, model, '{"factor":1000}', ranges, active)]})


def test_load_definition_reads_the_json_with_exact_numbers():
    definition = custom_indicators.load_definition(_definition(), "DFOCO")

    assert definition["fonte_id"] == 3
    assert definition["parameters"] == {"factor": 1000}
    assert definition["ranges"][0]["limit"] == Decimal("10.5")  # Decimal, não float


def test_load_definition_accepts_json_already_converted_by_the_driver():
    # Oracle 23: coluna com IS JSON chega como dict/list, não como texto
    cur = CursorFalso({"indicator_definition": [(3, "POINT_DENSITY", {"factor": 1000}, RANGES, 1)]})

    definition = custom_indicators.load_definition(cur, "DFOCO")

    assert definition["parameters"] == {"factor": 1000}
    assert definition["ranges"] == RANGES


def test_load_definition_refuses_missing_inactive_and_other_models():
    with pytest.raises(IndicatorError, match="não encontrado"):
        custom_indicators.load_definition(CursorFalso(), "NADA")
    with pytest.raises(IndicatorError, match="desativado"):
        custom_indicators.load_definition(_definition(active=0), "DFOCO")
    with pytest.raises(IndicatorError, match="AREA_COVERAGE"):
        custom_indicators.load_definition(_definition(model="AREA_COVERAGE"), "DFOCO")


# ---- ler os pontos

def _wkb_ponto(x, y):
    import shapely
    return shapely.to_wkb(shapely.Point(x, y))


def test_load_points_uses_the_newest_version_of_the_source():
    cur = CursorFalso({"input_feature": [(10, 1, _wkb_ponto(0, 0)), (12, 1, _wkb_ponto(1, 1))]})

    pontos, versao = custom_indicators.load_points(cur, 3)

    assert len(pontos) == 2
    assert versao == 12


def test_load_points_refuses_source_without_data_or_with_polygons():
    with pytest.raises(IndicatorError, match="não tem dados"):
        custom_indicators.load_points(CursorFalso(), 3)
    with pytest.raises(IndicatorError, match="não são pontos"):
        custom_indicators.load_points(CursorFalso({"input_feature": [(10, 3, _wkb_ponto(0, 0))]}), 3)


# ---- gravar

def test_save_rows_commits_one_batch_at_a_time():
    con = ConexaoFalsa()
    rows = [{"cod_imovel": f"PR-{i}"} for i in range(5)]

    gravados = custom_indicators.save_rows(con, rows, lote=2)

    assert gravados == 5
    assert [len(lote) for lote in con.cursor_falso.lotes] == [2, 2, 1]
    assert con.commits == 3  # um commit por lote
