"""IRL e IFC lidos e gravados (irl_ifc.py), sem banco: um cursor falso responde às consultas e
guarda o que seria gravado. Os números são os do IndicadorCalculoTest do back."""

import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dags"))

from georural import irl_ifc  # noqa: E402
from georural.irl_ifc import RuleError  # noqa: E402

REGRA_IRL = {"id": 1, "codigo": "RN-IRL-Min20pct", "versao": 1, "unidade": "%",
             "parametros": {"percentual_minimo": 20}}
REGRA_IFC = {"id": 2, "codigo": "RN-IFC-Queimadas", "versao": 1, "unidade": "focos/1000ha",
             "parametros": {"por_hectares": 1000, "janela_meses": 12, "satelite": "AQUA_M-T",
                            "limite_critico": 20}}


class CursorFalso:
    def __init__(self, respostas=None):
        self.respostas = respostas or {}
        self.outputtypehandler = None
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
        self.cursor_falso.lotes = []
        self.commits = 0

    def cursor(self):
        return self.cursor_falso

    def commit(self):
        self.commits += 1


# ---- regra vigente

def test_load_rule_reads_the_json_already_converted():
    cur = CursorFalso({"regra_calculo": [(1, "RN-IRL-Min20pct", 1, "%", {"percentual_minimo": 20})]})

    rule = irl_ifc.load_rule(cur, "IRL")

    assert rule["id"] == 1
    assert irl_ifc.parameter(rule, "percentual_minimo") == Decimal("20")


def test_load_rule_reads_the_json_as_text_too():
    cur = CursorFalso({"regra_calculo": [(1, "RN-IRL-Min20pct", 1, "%", '{"percentual_minimo": 20.5}')]})

    assert irl_ifc.parameter(irl_ifc.load_rule(cur, "IRL"), "percentual_minimo") == Decimal("20.5")


def test_without_active_rule_or_parameter_is_an_error():
    with pytest.raises(RuleError, match="Nenhuma regra ativa"):
        irl_ifc.load_rule(CursorFalso(), "IRL")
    with pytest.raises(RuleError, match="não tem o parâmetro percentual_minimo"):
        irl_ifc.parameter({**REGRA_IRL, "parametros": {}}, "percentual_minimo")


# ---- IRL

def test_irl_rows_match_the_plsql_cases():
    linhas = [
        # cod_imovel, area_ha, versao_imovel, area_rl, versao_rl
        ("COM_RL", Decimal("100.5"), 7, Decimal("15.5"), 9),
        ("SEM_RL", Decimal("50"), 7, None, 8),            # município coberto: versão do município
        ("RL_MAIOR", Decimal("10"), 7, Decimal("15"), 9),
    ]

    rows = {r["cod_imovel"]: r for r in irl_ifc.irl_rows(REGRA_IRL, linhas)}

    assert rows["COM_RL"]["valor"] == Decimal("15.4229")
    assert rows["COM_RL"]["status"] == "CRITICO"
    assert rows["COM_RL"]["saldo"] == Decimal("-4.6")
    assert rows["SEM_RL"]["valor"] == 0
    assert rows["SEM_RL"]["saldo"] == Decimal("-10")
    assert rows["SEM_RL"]["versao"] == 8
    assert rows["RL_MAIOR"]["valor"] == 100
    assert rows["RL_MAIOR"]["numerador"] == Decimal("10")
    assert rows["RL_MAIOR"]["status"] == "CONFORME"


def test_irl_row_has_everything_the_merge_needs():
    row = irl_ifc.irl_rows(REGRA_IRL, [("COM_RL", Decimal("100.5"), 7, Decimal("15.5"), 9)])[0]

    assert row == {
        "cod_imovel": "COM_RL", "indicador": "IRL", "regra_id": 1, "versao": 9, "versao_imovel": 7,
        "valor": Decimal("15.4229"), "status": "CRITICO", "numerador": Decimal("15.5"),
        "area_imovel": Decimal("100.5"), "saldo": Decimal("-4.6"), "unidade": "%", "regra": "RN-IRL-Min20pct",
    }


# ---- IFC

def test_ifc_rows_match_the_plsql_cases():
    properties = pd.DataFrame({
        "cod_imovel": ["COM_FOCO", "SEM_FOCO"],
        "area_ha": [Decimal("100.5"), Decimal("50")],
        "versao_imovel_id": [7, 7],
    })

    rows = {r["cod_imovel"]: r for r in irl_ifc.ifc_rows(REGRA_IFC, properties, pd.Series({"COM_FOCO": 2}), 33)}

    assert rows["COM_FOCO"]["valor"] == Decimal("19.9005")
    assert rows["COM_FOCO"]["status"] == "ALERTA"
    assert rows["COM_FOCO"]["numerador"] == Decimal("2")
    assert rows["COM_FOCO"]["saldo"] is None   # o IFC não tem saldo
    assert rows["COM_FOCO"]["versao"] == 33    # a versão mais nova dos focos
    assert rows["SEM_FOCO"]["status"] == "CONFORME"


# ---- gravar

def test_save_rows_commits_one_batch_at_a_time():
    con = ConexaoFalsa()

    gravados = irl_ifc.save_rows(con, [{"cod_imovel": f"PR-{i}"} for i in range(5)], lote=2)

    assert gravados == 5
    assert [len(lote) for lote in con.cursor_falso.lotes] == [2, 2, 1]
    assert con.commits == 3
