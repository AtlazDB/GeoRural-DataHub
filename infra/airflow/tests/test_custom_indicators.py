"""Indicadores cadastrados (custom_indicators.py), sem banco: um cursor falso responde às consultas
e guarda o que seria gravado."""

import sys
from datetime import date
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


def test_load_definition_refuses_missing_inactive_and_unknown_models():
    with pytest.raises(IndicatorError, match="não encontrado"):
        custom_indicators.load_definition(CursorFalso(), "NADA")
    with pytest.raises(IndicatorError, match="desativado"):
        custom_indicators.load_definition(_definition(active=0), "DFOCO")
    with pytest.raises(IndicatorError, match="desconhecido"):
        custom_indicators.load_definition(_definition(model="FREE_FORMULA"), "DFOCO")


def test_load_definition_accepts_area_coverage_and_reads_the_category():
    cur = CursorFalso({"indicator_definition": [(3, "AREA_COVERAGE", {"category": "MATA"}, RANGES, 1)]})

    definition = custom_indicators.load_definition(cur, "COBMATA")

    assert definition["model"] == "AREA_COVERAGE"
    assert definition["category"] == "MATA"


def test_load_definition_without_category_does_not_filter():
    definition = custom_indicators.load_definition(_definition(), "DFOCO")

    assert definition["category"] is None


# ---- ler as feições

def _wkb_ponto(x, y):
    import shapely
    return shapely.to_wkb(shapely.Point(x, y))


def _wkb_caixa(x0, y0, x1, y1):
    import shapely
    return shapely.to_wkb(shapely.box(x0, y0, x1, y1))


def test_load_features_uses_the_newest_version_of_the_source():
    cur = CursorFalso({"input_feature": [(10, 1, _wkb_ponto(0, 0)), (12, 1, _wkb_ponto(1, 1))]})

    pontos, versao = custom_indicators.load_features(cur, 3, "POINT_DENSITY")

    assert len(pontos) == 2
    assert versao == 12


def test_load_features_area_coverage_takes_polygons():
    cur = CursorFalso({"input_feature": [(10, 3, _wkb_caixa(0, 0, 1, 1)), (10, 7, _wkb_caixa(2, 2, 3, 3))]})

    poligonos, _ = custom_indicators.load_features(cur, 3, "AREA_COVERAGE")

    assert len(poligonos) == 2


def test_load_features_refuses_source_without_data_or_with_the_wrong_type():
    with pytest.raises(IndicatorError, match="não tem dados"):
        custom_indicators.load_features(CursorFalso(), 3, "POINT_DENSITY")
    with pytest.raises(IndicatorError, match="com a categoria 'MATA'"):
        custom_indicators.load_features(CursorFalso(), 3, "AREA_COVERAGE", "MATA")
    with pytest.raises(IndicatorError, match="precisa de pontos"):
        custom_indicators.load_features(CursorFalso({"input_feature": [(10, 3, _wkb_caixa(0, 0, 1, 1))]}),
                                        3, "POINT_DENSITY")
    with pytest.raises(IndicatorError, match="precisa de polígonos"):
        custom_indicators.load_features(CursorFalso({"input_feature": [(10, 1, _wkb_ponto(0, 0))]}),
                                        3, "AREA_COVERAGE")


def test_category_goes_as_a_sql_parameter():
    class CursorQueGuarda(CursorFalso):
        def execute(self, sql, **binds):
            self.sql, self.binds = sql, binds
            super().execute(sql, **binds)

    cur = CursorQueGuarda({"input_feature": [(10, 1, _wkb_ponto(0, 0))]})

    custom_indicators.load_features(cur, 3, "POINT_DENSITY", "AQUA_M-T")

    assert cur.binds["categoria"] == "AQUA_M-T"
    assert "AQUA_M-T" not in cur.sql  # nada concatenado
    assert ":categoria IS NULL OR f.categoria = :categoria" in cur.sql


# ---- filtro por data (parameters.since, IDesmat)

def test_load_definition_reads_since_as_a_date():
    cur = CursorFalso({"indicator_definition": [(3, "AREA_COVERAGE", {"since": "2008-07-22"}, RANGES, 1)]})

    definition = custom_indicators.load_definition(cur, "DESM08")

    assert definition["since"] == date(2008, 7, 22)


def test_load_definition_without_since_does_not_filter():
    definition = custom_indicators.load_definition(_definition(), "DFOCO")

    assert definition["since"] is None


def test_load_definition_refuses_an_invalid_since():
    cur = CursorFalso({"indicator_definition": [(3, "AREA_COVERAGE", {"since": "22/07/2008"}, RANGES, 1)]})

    with pytest.raises(IndicatorError, match="data inválida"):
        custom_indicators.load_definition(cur, "DESM08")


def test_since_goes_as_a_sql_parameter_in_both_queries():
    class CursorQueGuarda(CursorFalso):
        def __init__(self, respostas):
            super().__init__(respostas)
            self.chamadas = []

        def execute(self, sql, **binds):
            self.chamadas.append((sql, binds))
            super().execute(sql, **binds)

    feicoes = CursorQueGuarda({"input_feature": [(10, 3, _wkb_caixa(0, 0, 1, 1))]})
    imoveis = CursorQueGuarda({})

    custom_indicators.load_features(feicoes, 3, "AREA_COVERAGE", since=date(2008, 7, 22))
    custom_indicators.load_candidates(imoveis, 3, since=date(2008, 7, 22))

    chamadas = feicoes.chamadas + imoveis.chamadas
    assert len(chamadas) == 2
    for sql, binds in chamadas:
        assert binds["desde"] == date(2008, 7, 22)
        assert ":desde IS NULL OR f.data_referencia >= :desde" in sql


def test_without_since_the_date_parameter_goes_empty():
    class CursorQueGuarda(CursorFalso):
        def execute(self, sql, **binds):
            self.binds = binds
            super().execute(sql, **binds)

    cur = CursorQueGuarda({"input_feature": [(10, 1, _wkb_ponto(0, 0))]})

    custom_indicators.load_features(cur, 3, "POINT_DENSITY")

    assert cur.binds["desde"] is None


# ---- várias fontes (parameters.extraSources, ISAP)

def test_load_definition_without_extra_sources_uses_only_the_main_one():
    definition = custom_indicators.load_definition(_definition(), "DFOCO")

    assert definition["sources"] == [3]


def test_load_definition_adds_extra_sources_without_repeating():
    cur = CursorFalso({"indicator_definition": [
        (3, "AREA_COVERAGE", {"extraSources": [12, "15", 3, 12]}, RANGES, 1)]})

    definition = custom_indicators.load_definition(cur, "ISAP")

    assert definition["sources"] == [3, 12, 15]


def test_load_definition_refuses_invalid_extra_sources():
    for extras in ("12,15", [12, "UC"]):
        cur = CursorFalso({"indicator_definition": [(3, "AREA_COVERAGE", {"extraSources": extras}, RANGES, 1)]})
        with pytest.raises(IndicatorError, match="extraSources"):
            custom_indicators.load_definition(cur, "ISAP")


def test_each_source_goes_as_its_own_sql_parameter():
    class CursorQueGuarda(CursorFalso):
        def __init__(self, respostas):
            super().__init__(respostas)
            self.chamadas = []

        def execute(self, sql, **binds):
            self.chamadas.append((sql, binds))
            super().execute(sql, **binds)

    feicoes = CursorQueGuarda({"input_feature": [(10, 3, _wkb_caixa(0, 0, 1, 1)), (14, 7, _wkb_caixa(2, 2, 3, 3))]})
    imoveis = CursorQueGuarda({})

    poligonos, versao = custom_indicators.load_features(feicoes, [3, 12, 15], "AREA_COVERAGE")
    custom_indicators.load_candidates(imoveis, [3, 12, 15])

    assert len(poligonos) == 2
    assert versao == 14  # a mais nova entre as fontes
    for sql, binds in feicoes.chamadas + imoveis.chamadas:
        assert "a.fonte_id IN (:fonte0, :fonte1, :fonte2)" in sql
        assert (binds["fonte0"], binds["fonte1"], binds["fonte2"]) == (3, 12, 15)


def test_message_names_all_the_sources_without_data():
    with pytest.raises(IndicatorError, match="As fontes 3, 12 não têm dados"):
        custom_indicators.load_features(CursorFalso(), [3, 12], "AREA_COVERAGE")


# ---- cobertura dentro de uma camada base (BASE_COVERAGE, IAPP)

def test_load_definition_reads_the_base_sources():
    cur = CursorFalso({"indicator_definition": [
        (3, "BASE_COVERAGE", {"baseSources": [20, "21", 20]}, RANGES, 1)]})

    definition = custom_indicators.load_definition(cur, "IAPP")

    assert definition["model"] == "BASE_COVERAGE"
    assert definition["sources"] == [3]
    assert definition["base_sources"] == [20, 21]


def test_base_coverage_requires_valid_base_sources():
    for parameters in ({}, {"baseSources": []}, {"baseSources": 20}, {"baseSources": ["APP"]}):
        cur = CursorFalso({"indicator_definition": [(3, "BASE_COVERAGE", parameters, RANGES, 1)]})
        with pytest.raises(IndicatorError, match="baseSources"):
            custom_indicators.load_definition(cur, "IAPP")


def test_other_models_ignore_base_sources():
    definition = custom_indicators.load_definition(_definition(), "DFOCO")

    assert definition["base_sources"] == []


def test_base_coverage_rows_only_for_properties_with_app():
    coverage = pd.DataFrame({"cod_imovel": ["PR-1"], "base_ha": [Decimal("10")], "covered_ha": [Decimal("8")]})

    rows = custom_indicators.base_coverage_rows("IAPP", _properties(), coverage, 42, RANGES)

    assert len(rows) == 1
    assert rows[0]["cod_imovel"] == "PR-1"
    assert rows[0]["value"] == Decimal("80.0000")
    assert rows[0]["numerator"] == Decimal("8")    # APP com vegetação
    assert rows[0]["area"] == Decimal("10")        # APP total (o passivo é 10 − 8 = 2 ha)
    assert rows[0]["status"] == "CRITICO"


def test_message_says_which_date_had_no_data():
    with pytest.raises(IndicatorError, match="com a categoria 'MATA' a partir de 22/07/2008"):
        custom_indicators.load_features(CursorFalso(), 3, "AREA_COVERAGE", "MATA", date(2008, 7, 22))


# ---- cobertura de área (US13.T07)

def test_area_coverage_rows_measured_and_untouched():
    coverage = pd.DataFrame({"cod_imovel": ["PR-1"], "property_ha": [Decimal("200")],
                             "covered_ha": [Decimal("50")]})
    ranges = [{"status": "CRITICO", "limit": 10, "includeLimit": False},
              {"status": "ALERTA", "limit": 30, "includeLimit": False},
              {"status": "CONFORME"}]

    rows = {r["cod_imovel"]: r for r in custom_indicators.area_coverage_rows("COB", _properties(), coverage, 42, ranges)}

    assert rows["PR-1"]["value"] == Decimal("25.0000")   # 50 / 200 x 100
    assert rows["PR-1"]["status"] == "ALERTA"
    assert rows["PR-1"]["numerator"] == Decimal("50")
    assert rows["PR-1"]["area"] == Decimal("200")        # área do contorno, não a declarada (100,5)
    assert rows["PR-2"]["value"] == 0                    # não encosta na fonte
    assert rows["PR-2"]["status"] == "CRITICO"
    assert rows["PR-2"]["area"] is None                  # não foi medido
    assert len(rows) == 3


# ---- gravar

def test_save_rows_commits_one_batch_at_a_time():
    con = ConexaoFalsa()
    rows = [{"cod_imovel": f"PR-{i}"} for i in range(5)]

    gravados = custom_indicators.save_rows(con, rows, lote=2)

    assert gravados == 5
    assert [len(lote) for lote in con.cursor_falso.lotes] == [2, 2, 1]
    assert con.commits == 3  # um commit por lote
