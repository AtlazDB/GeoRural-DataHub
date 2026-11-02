"""As regras do mapeamento em Python (regras.py), sem banco: cada teste é um comportamento que o
pkg_quality.validate tinha e que a carga e o Revalidar precisam manter."""

import re
from datetime import datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, Point, Polygon, box

from georural import regras
from georural.regras import Contexto, Regra


def _validar(linhas: list[dict], lista: list[Regra], geometrias=None, ctx: Contexto | None = None):
    colunas = sorted({r.coluna for r in lista if r.tipo != "GEOMETRIA"})
    textos = pd.DataFrame([{c: l.get(c) for c in colunas} for l in linhas], columns=colunas)
    geos = np.array(geometrias if geometrias is not None else [None] * len(linhas), dtype=object)
    return regras.validar(textos, geos, lista, ctx or Contexto())


def _erros(v) -> list[tuple]:
    return [(e.linha, e.tipo_erro, e.campo) for e in v.erros.itertuples()]


def _regra(coluna, tipo="TEXTO", id_=1, **kw) -> Regra:
    return Regra(id=id_, coluna=coluna, tabela=kw.pop("tabela", "imovel_rural"), campo=kw.pop("campo", coluna),
                 tipo=tipo, **kw)


# ------------------------------------------------------------------ obrigatório e tipo

def test_obrigatorio_separa_ausente_de_em_branco():
    v = _validar([{"a": None}, {"a": ""}, {"a": "   "}, {"a": "N/A"}, {"a": "null"}, {"a": "ok"}],
                 [_regra("a", obrigatorio=True)])

    assert _erros(v) == [(0, "CAMPO_OBRIGATORIO_AUSENTE", "a"), (1, "CAMPO_EM_BRANCO", "a"),
                         (2, "CAMPO_EM_BRANCO", "a"), (3, "CAMPO_EM_BRANCO", "a"), (4, "CAMPO_EM_BRANCO", "a")]
    assert v.erros.iloc[3].valor_recebido == "N/A"


@pytest.mark.parametrize("tipo, formato, bons, ruins", [
    ("INTEIRO", None, ["12", "-3", "+7"], ["1.5", "1e3", "abc"]),
    ("DECIMAL", None, ["1.5", "-0.25", ".5", "10"], ["1,5", "1.2.3", "x"]),
    ("DECIMAL", "VIRGULA", ["25,9", "25.9", "3"], ["25,9,1", "abc"]),
    ("BOOLEANO", None, ["SIM", "não", "NAO", "sim"], ["S", "true"]),
    ("DATA", None, ["01/02/2020", "29/02/2024"], ["1/2/2020", "31/02/2020", "2020-02-01"]),
    ("DATA", "YYYY/MM/DD HH24:MI:SS|DD/MM/YYYY HH24:MI:SS",
     ["2015/03/11 17:19:00", "11/12/2025 17:11:00"], ["2015-03-11 17:19:00", "11/12/2025"]),
    ("DATA", "EPOCH_MS", ["1700000000000"], ["-5", "1.5", "1234567890123456"]),
])
def test_tipo_de_dado(tipo, formato, bons, ruins):
    v = _validar([{"x": t} for t in bons + ruins], [_regra("x", tipo, formato=formato)])

    assert [e.linha for e in v.erros.itertuples()] == list(range(len(bons), len(bons) + len(ruins)))
    assert set(v.erros.tipo_erro) == {"TIPO_DE_DADO_INVALIDO"}
    assert v.erros.iloc[0].motivo == f"Valor não converte para {tipo}."


def test_tipo_nao_confere_vazio_nem_falso_nulo():
    v = _validar([{"x": None}, {"x": " "}, {"x": "-"}], [_regra("x", "INTEIRO")])
    assert v.erros.empty


def test_mascara_de_data_desconhecida_e_erro_de_configuracao():
    with pytest.raises(regras.RegraInvalida):
        _validar([{"x": "2020"}], [_regra("x", "DATA", formato="YYYY-WW")])


# ------------------------------------------------------------------ chave e regressão

def test_chave_composta_na_ordem_da_fonte_e_repetidas_reprovadas():
    lista = [_regra("data", id_=1, chave=True, ordem=2), _regra("sat", id_=2, chave=True, ordem=1)]
    v = _validar([{"data": "d1", "sat": "A"}, {"data": "d1", "sat": "A"}, {"data": "d2", "sat": "A"},
                  {"data": None, "sat": "A"}, {"data": None, "sat": "A"}], lista)

    assert list(v.chave_natural) == ["A|d1", "A|d1", "A|d2", None, None]  # parte faltando: sem chave
    assert _erros(v) == [(0, "CHAVE_NATURAL_DUPLICADA", "sat, data"), (1, "CHAVE_NATURAL_DUPLICADA", "sat, data")]


def test_regressao_quando_o_valor_some():
    lista = [_regra("cod", id_=1, chave=True), _regra("area", id_=2, campo="area_ha", bloquear_regressao=True)]
    ctx = Contexto(anteriores={"area_ha": {"PR-1": "10.5", "PR-2": "3"}})
    v = _validar([{"cod": "PR-1", "area": None}, {"cod": "PR-2", "area": "4"}, {"cod": "PR-3", "area": None}],
                 lista, ctx=ctx)

    assert _erros(v) == [(0, "REGRESSAO_DE_VALOR", "area")]
    assert v.erros.iloc[0].motivo == "Campo preenchido na carga anterior chegou vazio. Valor anterior: 10.5"


# ------------------------------------------------------------------ geometria

QUADRADO = box(-51.0, -25.0, -50.99, -24.99)
GRAVATA = Polygon([(-51.0, -25.0), (-50.99, -24.99), (-50.99, -25.0), (-51.0, -24.99), (-51.0, -25.0)])


def _geo(**kw) -> Regra:
    return _regra("geometry", "GEOMETRIA", id_=9, campo="geometria", obrigatorio=True, **kw)


def test_geometria_nula_e_do_tipo_errado():
    v = _validar([{}, {}, {}], [_geo(formato="POLIGONO")], [None, Point(-51, -25), QUADRADO])

    assert _erros(v) == [(0, "GEOMETRIA_NULA", "geometry"), (1, "GEOMETRIA_TIPO_INCOMPATIVEL", "geometry")]
    assert v.erros.iloc[1].motivo == "Geometria do tipo PONTO; esperado POLIGONO."


def test_geometria_invalida_vai_para_a_quarentena_com_o_ponto_do_problema():
    v = _validar([{}], [_geo(politica="QUARENTENA")], [GRAVATA])

    assert _erros(v) == [(0, "GEOMETRIA_INVALIDA", "geometry")]
    # o front marca o ponto em vermelho a partir do "[x y]" do motivo (quarentena.js, pontoDoMotivo)
    assert re.search(r"\[\s*-?\d+(\.\d+)?[,\s]+-?\d+(\.\d+)?\s*\]", v.erros.iloc[0].motivo)
    assert v.correcoes.empty


def test_geometria_invalida_e_corrigida_e_registrada():
    v = _validar([{}], [_geo(politica="CORRIGIR_E_REGISTRAR")], [GRAVATA])

    assert v.erros.empty
    assert v.geometria[0].is_valid and v.geometria[0].geom_type in ("Polygon", "MultiPolygon")
    assert len(v.correcoes) == 1 and v.correcoes.iloc[0].geometria_original.equals(GRAVATA)


def test_correcao_que_perde_area_demais_nao_vale(monkeypatch):
    medidas = iter([100.0, 98.0])  # antes, depois: perdeu 2%
    monkeypatch.setattr(regras, "area_m2", lambda _g: next(medidas))
    v = _validar([{}], [_geo(politica="CORRIGIR_E_REGISTRAR")], [GRAVATA])

    assert _erros(v) == [(0, "GEOMETRIA_INVALIDA", "geometry")]


def test_fora_do_pr_com_margem_de_1_km():
    recorte = box(-51.0, -25.0, -50.0, -24.0)
    pertinho = Point(-49.995, -24.5)   # ~500 m a leste da divisa
    longe = Point(-49.9, -24.5)        # ~10 km
    ctx = Contexto(recorte_pr=recorte)
    v = _validar([{}, {}, {}], [_geo(formato="PONTO", recortar_pr=True)], [Point(-50.5, -24.5), pertinho, longe], ctx=ctx)

    assert _erros(v) == [(2, "FORA_DO_RECORTE_TERRITORIAL", "geometry")]


def test_sem_municipio_do_pr_a_carga_falha():
    with pytest.raises(regras.RegraInvalida, match="Nenhum município do PR"):
        _validar([{}], [_geo(recortar_pr=True)], [QUADRADO])


def test_formato_de_geometria_desconhecido():
    with pytest.raises(regras.RegraInvalida):
        _validar([{}], [_geo(formato="CIRCULO")], [QUADRADO])


def test_multipoligono_vale_como_poligono():
    v = _validar([{}], [_geo(formato="POLIGONO")], [MultiPolygon([QUADRADO])])
    assert v.erros.empty


# ------------------------------------------------------------------ domínio, CPF, referência

def test_dominio_confere_valor_informado_mesmo_falso_nulo():
    ctx = Contexto(dominios={7: {"AT", "PE", "CA", "SU"}})
    v = _validar([{"s": "AT"}, {"s": "XX"}, {"s": "N/A"}, {"s": " "}, {"s": None}], [_regra("s", dominio_id=7)], ctx=ctx)

    assert _erros(v) == [(1, "VALOR_FORA_DO_DOMINIO", "s"), (2, "VALOR_FORA_DO_DOMINIO", "s")]


@pytest.mark.parametrize("valor, valido", [
    ("529.982.247-25", True), ("52998224725", True), ("11.222.333/0001-81", True),
    ("111.111.111-11", False), ("529.982.247-26", False), ("***.982.247-**", False), ("123", False),
])
def test_cpf_cnpj(valor, valido):
    assert regras.cpf_cnpj_valido(valor) is valido


def test_referencia_ao_municipio_pelo_codigo_ou_pelo_nome_sem_acento():
    ctx = Contexto(municipios_ibge={"4115200"}, municipios_pr={regras.sem_acento("Maringá")})
    v = _validar([{"m": "4115200"}, {"m": "MARINGA"}, {"m": "Maringá"}, {"m": "Atlantida"}, {"m": "-"}],
                 [_regra("m", referencia="municipio")], ctx=ctx)

    assert _erros(v) == [(3, "REFERENCIA_NAO_ENCONTRADA", "m")]
    assert v.erros.iloc[0].motivo == "Valor não encontrado em municipio."


# ------------------------------------------------------------------ quarentena e conversão

def test_quarentena_junta_os_motivos_e_guarda_o_primeiro_erro():
    lista = [_regra("a", id_=1, obrigatorio=True), _regra("b", "INTEIRO", id_=2)]
    v = _validar([{"a": None, "b": "x"}, {"a": "ok", "b": "1"}], lista)
    linha = regras.motivo_da_linha(v.erros).iloc[0]

    assert (linha.linha, linha.campo, linha.tipo_erro) == (0, "a", "CAMPO_OBRIGATORIO_AUSENTE")
    assert linha.motivo == "Campo obrigatório não informado. | Valor não converte para INTEIRO."


def test_converter_para_o_tipo_do_destino():
    assert regras.converter(" 12 ", _regra("x", "INTEIRO")) == 12
    assert regras.converter("25,9", _regra("x", "DECIMAL", formato="VIRGULA")) == Decimal("25.9")
    assert regras.converter("487.38854000000003", _regra("x", "DECIMAL")) == Decimal("487.38854000000003")
    assert regras.converter("N/A", _regra("x", "DECIMAL")) is None
    assert regras.converter("Não", _regra("x", "BOOLEANO")) == 0
    assert regras.converter("11/12/2025 17:11:00",
                            _regra("x", "DATA", formato="YYYY/MM/DD HH24:MI:SS|DD/MM/YYYY HH24:MI:SS")) \
        == datetime(2025, 12, 11, 17, 11)
    assert regras.converter("0", _regra("x", "DATA", formato="EPOCH_MS")) == datetime(1970, 1, 1)
    assert regras.converter("x" * 4001, _regra("x")) is None  # o JSON_VALUE devolvia nulo


def test_campos_derivados():
    assert regras.campos_derivados("imovel_rural", {"cod_imovel": "PR-4113700-ABC"}) == {"estado": "PR"}
    assert regras.campos_derivados("municipio", {"cod_ibge": "4115200"}) == {"estado": "PR"}
    assert regras.campos_derivados("municipio", {"cod_ibge": "4115200", "estado": "SC"}) == {}


def test_repetidas_no_arquivo_inteiro_valem_dentro_do_bloco():
    lista = [_regra("cod", chave=True)]
    v = _validar([{"cod": "A"}, {"cod": "B"}], lista)
    assert v.erros.empty
    textos = pd.DataFrame([{"cod": "A"}, {"cod": "B"}])
    v = regras.validar(textos, np.array([None, None], dtype=object), lista, Contexto(), repetidas={"B"})
    assert _erros(v) == [(1, "CHAVE_NATURAL_DUPLICADA", "cod")]
