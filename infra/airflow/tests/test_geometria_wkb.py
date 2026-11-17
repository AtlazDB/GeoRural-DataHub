"""Preenchimento da geometria_wkb das linhas antigas (geometria_wkb.py), sem banco."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dags"))

from georural import geometria_wkb  # noqa: E402


class CursorFalso:
    def __init__(self, con):
        self.con = con
        self.arraysize = 100

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, **_):
        self.con.consultas.append(sql)

    def fetchall(self):
        return [(rid,) for rid in self.con.pendentes]

    def executemany(self, sql, linhas):
        self.con.lotes.append((sql, linhas))


class ConexaoFalsa:
    def __init__(self, pendentes):
        self.pendentes = pendentes
        self.consultas, self.lotes = [], []
        self.commits = 0

    def cursor(self):
        return CursorFalso(self)

    def commit(self):
        self.commits += 1


def test_preenche_em_lotes_com_commit_em_cada_um():
    con = ConexaoFalsa([f"r{i}" for i in range(5)])

    preenchidas = geometria_wkb.fill_table(con, "imovel_rural", lote=2)

    assert preenchidas == 5
    assert [len(linhas) for _, linhas in con.lotes] == [2, 2, 1]
    assert con.commits == 3
    sql = con.lotes[0][0]
    assert "SDO_UTIL.TO_WKBGEOMETRY(geometria)" in sql  # a conversão é do Oracle
    assert "geometria_wkb IS NULL" in sql               # rodar de novo não regrava


def test_so_procura_linha_com_geometria_e_sem_wkb():
    con = ConexaoFalsa([])

    assert geometria_wkb.fill_table(con, "input_feature", lote=10) == 0
    assert "geometria IS NOT NULL AND geometria_wkb IS NULL" in con.consultas[0]
    assert con.lotes == [] and con.commits == 0


def test_tabela_sem_a_coluna_e_recusada():
    with pytest.raises(ValueError, match="não tem geometria_wkb"):
        geometria_wkb.fill_table(ConexaoFalsa([]), "car_reserva_legal", lote=10)
