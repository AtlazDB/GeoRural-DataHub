"""Município do imóvel (municipalities.py): o UPDATE pelo código IBGE dentro do cod_imovel."""

import oracledb
import pytest

from georural import generico, gravacao, municipalities


class _CursorFalso:
    def __init__(self, linhas=0, erro=None):
        self.sql = []
        self.rowcount = linhas
        self.erro = erro

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, **_):
        if self.erro:
            raise self.erro
        self.sql.append(sql)


class _ConexaoFalsa:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_preenche_e_devolve_quantos_imoveis_ganharam_municipio():
    cur = _CursorFalso(linhas=559_357)
    con = _ConexaoFalsa(cur)

    assert municipalities.resolve_municipalities(con) == 559_357
    assert con.commits == 1
    assert len(cur.sql) == 1


def test_so_mexe_em_quem_esta_sem_municipio_e_tem_codigo_do_car():
    sql = " ".join(municipalities.SQL_RESOLVE.split())
    assert "WHERE i.municipio_id IS NULL" in sql
    assert "SUBSTR(i.cod_imovel, 4, 7)" in sql  # PR-4113700-... -> 4113700
    assert "REGEXP_LIKE(i.cod_imovel, '^[A-Z]{2}-[0-9]{7}-')" in sql
    # Município fora da tabela: o imóvel fica NULL em vez de ser regravado com NULL a cada carga.
    assert "AND EXISTS (SELECT 1 FROM municipio m" in sql


def test_conjuntos_batem_com_os_da_gravacao():
    assert municipalities.CONJUNTOS == {
        gravacao.CONJUNTO_DA_TABELA["imovel_rural"],
        gravacao.CONJUNTO_DA_TABELA["municipio"],
    }


def test_falha_ao_resolver_nao_derruba_a_carga():
    con = _ConexaoFalsa(_CursorFalso(erro=oracledb.DatabaseError("ORA-00054: resource busy")))

    assert generico._resolver_municipios(con) is None
    assert con.rollbacks == 1
    assert con.commits == 0


@pytest.mark.parametrize("conjunto", ["reserva_legal", "focos_calor", "insumo_mta"])
def test_outros_conjuntos_nao_resolvem_municipio(conjunto):
    assert conjunto not in municipalities.CONJUNTOS
