"""Partes do caminho genérico que não precisam de banco: leitura, tipos e desempate."""

import json
import zipfile

import pandas as pd
import pytest
import shapely
from shapely.geometry import Polygon

from georural import gravacao, leitura
from georural.colunas import Levantamento, tipo_percebido
from georural.validacao import ColunasDesempate, planejar_desempate


def _pagina(inicio: int, quantos: int, crs: str | None = None) -> bytes:
    dados = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": i, "properties": {"objectid": i, "nome": f"m{i}", "area": 1.5},
         "geometry": {"type": "Point", "coordinates": [-51.0 + i / 1000, -25.0]}}
        for i in range(inicio, inicio + quantos)
    ]}
    if crs:
        dados["crs"] = {"type": "name", "properties": {"name": crs}}
    return json.dumps(dados).encode()


def test_zip_da_coleta_le_as_paginas_em_ordem_e_ignora_o_manifesto(tmp_path):
    zip_ = tmp_path / "fonte.zip"
    with zipfile.ZipFile(zip_, "w") as z:
        z.writestr("parte-0002.geojson", _pagina(3, 2))
        z.writestr("parte-0001.geojson", _pagina(0, 3, "urn:ogc:def:crs:EPSG::4674"))
        z.writestr("coleta.json", json.dumps({"total": 5}))

    registros = list(leitura.registros(zip_, tmp_path / "x"))

    assert [r.linha for r in registros] == [1, 2, 3, 4, 5]
    assert [r.atributos["objectid"] for r in registros] == [0, 1, 2, 3, 4]
    assert registros[0].camada == "parte-0001.geojson"
    assert registros[0].geometria.x == pytest.approx(-51.0)  # 4674: sem reprojeção


def test_lotes_respeitam_o_tamanho(tmp_path):
    arquivo = tmp_path / "f.geojson"
    arquivo.write_bytes(_pagina(0, 7))
    assert [len(l) for l in leitura.lotes(arquivo, tmp_path / "x", 3)] == [3, 3, 1]


def test_geojson_em_outro_crs_e_reprojetado(tmp_path):
    arquivo = tmp_path / "utm.geojson"
    arquivo.write_text(json.dumps({"type": "FeatureCollection",
                                   "crs": {"type": "name", "properties": {"name": "EPSG:31982"}},
                                   "features": [{"type": "Feature", "properties": {},
                                                 "geometry": {"type": "Point", "coordinates": [500000, 7200000]}}]}))
    ponto = next(leitura.registros(arquivo, tmp_path / "x")).geometria
    assert -52 < ponto.x < -50 and -26 < ponto.y < -24


def test_formato_nao_suportado(tmp_path):
    arquivo = tmp_path / "dados.csv"
    arquivo.write_text("a,b\n1,2\n")
    with pytest.raises(leitura.ArquivoInvalido):
        list(leitura.registros(arquivo, tmp_path / "x"))


def test_texto_no_formato_das_regras():
    assert leitura.texto(1e-05) == "0.00001"
    assert leitura.texto(134.82004) == "134.82004"
    assert leitura.texto(4106407) == "4106407"
    assert leitura.texto(True) == "SIM"
    assert leitura.texto(float("nan")) is None
    assert json.loads(leitura.atributos_json({"a": 1.5, "b": None})) == {"a": "1.5", "b": None}


def test_wkb_vai_no_sentido_anti_horario_e_2d():
    horario = Polygon([(0, 0, 5), (0, 1, 5), (1, 1, 5), (1, 0, 5)])
    geometria = shapely.from_wkb(leitura.wkb(horario))
    assert geometria.exterior.is_ccw and not geometria.has_z


def test_tipo_percebido():
    assert tipo_percebido([1, 2]) == "INTEIRO"
    assert tipo_percebido([1, 2.5]) == "DECIMAL"
    assert tipo_percebido(["11/03/2015", "01/01/2020"]) == "DATA"
    assert tipo_percebido(["2014-05-07T20:52:55.333Z"]) == "DATA"
    assert tipo_percebido(["4106407"]) == "INTEIRO"
    assert tipo_percebido(["AT", "PE"]) == "TEXTO"
    assert tipo_percebido([]) == "TEXTO"


def test_levantamento_guarda_a_ordem_e_a_amostra():
    lev = Levantamento()
    lev.acrescentar({"b": 1, "a": None}, None)
    lev.acrescentar({"b": 2, "c": "x"}, shapely.Point(0, 0))
    assert lev.nomes == ["b", "a", "c"] and lev.amostra["b"] == [1, 2] and lev.amostra["a"] == []
    assert lev.tem_geometria


def test_desempate_com_as_colunas_do_wfs():
    """No WFS do SICAR: status_imovel, area e dat_criacao em ISO 8601."""
    cols = ColunasDesempate(codigo="cod_imovel", situacao="status_imovel", area="area",
                            atualizacao=None, criacao="dat_criacao")
    tabela = pd.DataFrame({
        "cod_imovel":    ["A", "A", "B", "B", "C", "D", "D"],
        "status_imovel": ["CA", "AT", "PE", "PE", "AT", "PE", "SU"],
        "area":          [10.0, 10.0, 5.0, 6.0, 1.0, 2.0, 2.5],
        "dat_criacao":   ["2020-01-01T00:00:00Z", "2019-01-01T00:00:00Z",
                          "2018-05-07T20:52:55.333Z", "2021-05-07T20:52:55.333Z", "2020-01-01T00:00:00Z",
                          "2018-01-01T00:00:00Z", "2022-03-01T10:00:00.000Z"],
    })

    plano = planejar_desempate(tabela, cols)

    assert set(plano) == {"A", "B", "D"}
    assert plano["A"].vencedora == 2 and 1 in plano["A"].descartes      # AT vence a CA
    assert plano["B"].descartes == {}                                   # mesma situação, área diferente: operador decide
    assert plano["D"].vencedora == 2 and "antiga" in plano["D"].descartes[1].motivo  # a mais recente (ISO) vence


def test_wkb_tira_vertice_repetido_abaixo_da_tolerancia_do_banco():
    """ORA-13356 na RL do IAT: dois vértices seguidos a menos de 5 cm. Sai sem mudar a forma."""
    quase_igual = -50.0 + 1e-8  # ~1 mm
    poligono = Polygon([(-51, -25), (-50, -25), (quase_igual, -25), (-50, -24), (-51, -24)])
    limpo = shapely.from_wkb(leitura.wkb(poligono))
    assert len(limpo.exterior.coords) == 5  # 4 vértices + o fechamento
    assert limpo.area == pytest.approx(poligono.area)


def test_wkb_anel_minusculo_que_colapsaria_vai_como_veio():
    """Um buraco de 1 mm some ao tirar os repetidos (o GEOS reclama): a geometria segue intacta."""
    furo = [(-50.5, -24.5), (-50.5 + 1e-8, -24.5), (-50.5 + 1e-8, -24.5 + 1e-8), (-50.5, -24.5)]
    poligono = Polygon([(-51, -25), (-50, -25), (-50, -24), (-51, -24)], [furo])
    resultado = shapely.from_wkb(leitura.wkb(poligono))
    assert resultado.geom_type == "Polygon" and len(resultado.interiors) == 1


def test_input_feature_tem_um_conjunto_por_fonte():
    assert gravacao.conjunto_da_carga("input_feature", "IAT-X") == "insumo_iat-x"
    assert gravacao.conjunto_da_carga("input_feature", "EMBARGO") == "insumo_embargo"


def test_tabelas_de_sempre_continuam_com_o_mesmo_conjunto():
    assert gravacao.conjunto_da_carga("car_reserva_legal", "IAT-X") == "reserva_legal"
    assert gravacao.conjunto_da_carga("imovel_rural", "CAR-WFS") == gravacao.CONJUNTO_IMOVEIS
    assert gravacao.conjunto_da_carga("foco_calor", "FOCOS-IAT") == "focos_calor"
    assert gravacao.conjunto_da_carga("municipio", "MUN-IAT") == "municipios"


def test_conjunto_do_insumo_cabe_na_coluna():
    # dataset_versao.conjunto é VARCHAR2(60); fonte.sigla, VARCHAR2(12)
    assert len(gravacao.conjunto_da_carga("input_feature", "X" * 12)) <= 60


# ---- indicadores depois da carga (US13.T10: irl_ifc.py no lugar do pkg_indicadores)

class _ConexaoFalsa:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def test_carga_recalcula_os_indicadores_do_conjunto(monkeypatch):
    from georural import generico, irl_ifc

    chamadas = []
    monkeypatch.setattr(irl_ifc, "calculate_on",
                        lambda con, indicadores, lote: chamadas.append((indicadores, lote)) or {i: 10 for i in indicadores})

    assert generico._recalcular_indicadores(_ConexaoFalsa(), "imoveis_rurais", 500) == "IRL: 10, IFC: 10"
    assert generico._recalcular_indicadores(_ConexaoFalsa(), "reserva_legal", 500) == "IRL: 10"
    assert generico._recalcular_indicadores(_ConexaoFalsa(), "focos_calor", 500) == "IFC: 10"
    assert chamadas == [(("IRL", "IFC"), 500), (("IRL",), 500), (("IFC",), 500)]


def test_conjunto_sem_indicador_nao_recalcula(monkeypatch):
    from georural import generico, irl_ifc

    monkeypatch.setattr(irl_ifc, "calculate_on", lambda *_: pytest.fail("não devia calcular"))

    assert generico._recalcular_indicadores(_ConexaoFalsa(), "municipios", 500) is None
    assert generico._recalcular_indicadores(_ConexaoFalsa(), None, 500) is None


def test_falha_nos_indicadores_nao_derruba_a_carga(monkeypatch):
    from georural import generico, irl_ifc

    def falha(*_):
        raise irl_ifc.RuleError("Nenhuma regra ativa para o indicador IRL")

    monkeypatch.setattr(irl_ifc, "calculate_on", falha)
    con = _ConexaoFalsa()

    assert generico._recalcular_indicadores(con, "reserva_legal", 500) == "falhou: Nenhuma regra ativa para o indicador IRL"
    assert con.rollbacks == 1
