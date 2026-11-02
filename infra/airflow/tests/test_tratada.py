"""A gravação incremental (tratada.py), a leitura em blocos e o desempate, sem banco: um banco falso
guarda as instruções, e o que importa é a decisão de inserir, alterar, deixar igual ou apagar."""

import json
import zipfile
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point, box

from georural import colunas, generico, leitura, revalidacao, sdo, tratada
from georural.regras import Regra


class CursorFalso:
    """Responde às consultas de colunas e do que a tabela já tem; guarda inserts, updates e deletes."""

    def __init__(self, banco):
        self.banco = banco
        self.outputtypehandler = None
        self._linhas = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def __iter__(self):
        return iter(self._linhas)

    def setinputsizes(self, **_):
        pass

    def execute(self, sql, **_binds):
        if "user_tab_columns" in sql:
            self._linhas = [(c,) for c in self.banco.colunas]
        else:
            self._linhas = self.banco.existentes

    def executemany(self, sql, linhas):
        tipo = sql.split()[0]
        self.banco.instrucoes[tipo].append(linhas)


class BancoFalso:
    def __init__(self, colunas_, existentes=()):
        self.colunas = colunas_
        self.existentes = list(existentes)
        self.instrucoes = {"INSERT": [], "UPDATE": [], "DELETE": []}
        self.commits = 0

    def cursor(self):
        return CursorFalso(self)

    def commit(self):
        self.commits += 1

    def gettype(self, _nome):
        return SimpleNamespace(newobject=lambda valores=None: SimpleNamespace(valores=valores))


def _regra(coluna, tipo="TEXTO", id_=1, tabela="foco_calor", **kw) -> Regra:
    return Regra(id=id_, coluna=coluna, tabela=tabela, campo=kw.pop("campo", coluna), tipo=tipo, **kw)


def _tratada(banco, regras, conjunto="c") -> tratada.Tratada:
    return tratada.Tratada(banco, regras, versao_id=9, conjunto=conjunto)


def _linhas(t, textos, geometria=None):
    destino = t.destinos[0]
    return {destino.tabela: [t.linha(destino, x, geometria) for x in textos]}


COLUNAS = ["objectid", "cod_imovel", "valor", "dataset_versao_id", "hash_registro", "geometria"]


def test_com_chave_insere_altera_e_deixa_igual():
    regras = [_regra("id", id_=1, campo="objectid", chave=True), _regra("v", id_=2, campo="valor")]
    banco = BancoFalso(COLUNAS)
    t = _tratada(banco, regras)
    destino = t.destinos[0]
    igual = t.linha(destino, {"id": "1", "v": "a"}, None)["hash"]
    banco2 = BancoFalso(COLUNAS, [("1", "rid1", igual), ("2", "rid2", "hash-velho"), ("3", "rid3", None)])
    t = _tratada(banco2, regras)

    contagem = t.gravar(_linhas(t, [{"id": "1", "v": "a"}, {"id": "2", "v": "b"}, {"id": "3", "v": "c"},
                                    {"id": "4", "v": "d"}]))

    assert (contagem.inseridos, contagem.alterados, contagem.iguais) == (1, 2, 1)
    assert [b["rid"] for b in banco2.instrucoes["UPDATE"][0]] == ["rid2", "rid3"]  # sem hash conta como alterada
    assert banco2.commits == 1
    assert t.finalizar().removidos == 0  # com chave nunca apaga
    assert banco2.instrucoes["DELETE"] == []


def test_alterar_nao_regrava_a_chave_e_marca_a_versao_nova():
    regras = [_regra("id", id_=1, campo="objectid", chave=True), _regra("v", id_=2, campo="valor")]
    banco = BancoFalso(COLUNAS, [("2", "rid2", "velho")])
    t = _tratada(banco, regras)

    t.gravar(_linhas(t, [{"id": "2", "v": "b"}]))

    binds = banco.instrucoes["UPDATE"][0][0]
    assert binds["v0"] == "b" and binds["v1"] == 9  # valor, dataset_versao_id
    assert "2" not in [v for k, v in binds.items() if k.startswith("v")]


def test_retrato_por_imovel_troca_so_o_que_mudou_e_nao_mexe_em_outro_imovel():
    regras = [_regra("cod", id_=1, campo="cod_imovel", tabela="car_reserva_legal"),
              _regra("v", id_=2, campo="valor", tabela="car_reserva_legal")]
    banco = BancoFalso(COLUNAS)
    t = _tratada(banco, regras)
    fica = t.linha(t.destinos[0], {"cod": "A", "v": "1"}, None)["hash"]
    banco = BancoFalso(COLUNAS, [("A", "r1", fica), ("A", "r2", "sumiu"), ("B", "r3", "outro-imovel")])
    t = _tratada(banco, regras)

    contagem = t.gravar(_linhas(t, [{"cod": "A", "v": "1"}, {"cod": "A", "v": "novo"}]))
    removidos = t.finalizar().removidos

    assert (contagem.inseridos, contagem.iguais, removidos) == (1, 1, 1)
    assert banco.instrucoes["DELETE"][0] == [{"rid": "r2"}]  # r3 é de um imóvel que não veio


def test_retrato_do_conjunto_apaga_o_que_nao_veio_e_conta_repetidas():
    regras = [_regra("v", id_=1, campo="valor", tabela="input_feature")]
    banco = BancoFalso(COLUNAS)
    t = _tratada(banco, regras)
    h = t.linha(t.destinos[0], {"v": "x"}, None)["hash"]
    banco = BancoFalso(COLUNAS, [("r1", h), ("r2", h), ("r3", "sumiu")])
    t = _tratada(banco, regras)

    contagem = t.gravar(_linhas(t, [{"v": "x"}, {"v": "x"}, {"v": "x"}]))
    removidos = t.finalizar().removidos

    assert (contagem.iguais, contagem.inseridos, removidos) == (2, 1, 1)  # 2 já existiam, a 3ª cópia entra
    assert banco.instrucoes["DELETE"][0] == [{"rid": "r3"}]


def test_estado_vem_em_todas_as_linhas_mesmo_sem_poder_deduzir():
    regras = [_regra("cod", id_=1, campo="cod_imovel", tabela="imovel_rural", chave=True)]
    banco = BancoFalso(["cod_imovel", "estado", "dataset_versao_id", "hash_registro"])
    t = _tratada(banco, regras)

    t.gravar(_linhas(t, [{"cod": "PR-1"}, {"cod": "sem-traco"}]))

    linhas = banco.instrucoes["INSERT"][0]
    assert [sorted(k for k in b if k.startswith("v")) for b in linhas] == [["v0", "v1", "v2"]] * 2  # mesmas colunas
    assert (linhas[0]["v1"], linhas[1]["v1"]) == ("PR", None)


def test_hash_muda_com_valor_ou_geometria_e_nao_com_a_forma_do_numero():
    base = tratada.hash_registro({"a": Decimal("10.50")}, b"g")
    assert base == tratada.hash_registro({"a": Decimal("10.5")}, b"g")
    assert base == tratada.hash_registro({"a": 10.5}, b"g")
    assert base != tratada.hash_registro({"a": Decimal("10.51")}, b"g")
    assert base != tratada.hash_registro({"a": Decimal("10.5")}, b"h")


def test_canonico():
    assert tratada.canonico(None) is None
    assert tratada.canonico(Decimal("100")) == "100"  # sem notação científica
    assert tratada.canonico(Decimal("-0.0")) == "0"
    assert tratada.canonico(5) == "5"
    assert tratada.canonico(datetime(2025, 12, 11, 17, 11)) == "2025-12-11T17:11:00"


# ------------------------------------------------------------------ SDO_GEOMETRY

def test_sdo_ponto_linha_e_multi():
    assert sdo.elementos(MultiPoint([(1, 2), (3, 4)])) == (2005, [1, 1, 2], [1.0, 2.0, 3.0, 4.0])
    assert sdo.elementos(LineString([(1, 2), (3, 4)])) == (2002, [1, 2, 1], [1.0, 2.0, 3.0, 4.0])


def test_sdo_ponto_vai_no_sdo_point():
    # A API dos focos lê geometria.sdo_point.x e .y: em SDO_ORDINATES, o mapa fica sem os focos.
    objeto = sdo.TiposSdo(BancoFalso([])).converter(Point(-51, -25))

    assert (objeto.SDO_GTYPE, objeto.SDO_SRID) == (2001, 4326)
    assert (objeto.SDO_POINT.X, objeto.SDO_POINT.Y) == (-51.0, -25.0)
    assert not hasattr(objeto, "SDO_ORDINATES")


def test_sdo_converte_vazio_em_nulo():
    tipos = sdo.TiposSdo(BancoFalso([]))
    assert tipos.converter(None) is None
    assert tipos.converter(MultiPolygon()) is None


# ------------------------------------------------------------------ leitura em blocos

def _pagina(inicio: int, quantos: int) -> bytes:
    return json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"objectid": i, "area": 0 if i % 2 else 1.5},
         "geometry": {"type": "Point", "coordinates": [-51.0, -25.0]}}
        for i in range(inicio, inicio + quantos)]}).encode()


def test_tabelas_entrega_o_mesmo_que_registros(tmp_path):
    zip_ = tmp_path / "fonte.zip"
    with zipfile.ZipFile(zip_, "w") as z:
        z.writestr("parte-0001.geojson", _pagina(0, 5))
        z.writestr("parte-0002.geojson", _pagina(5, 2))
        z.writestr("coleta.json", "{}")

    antigos = list(leitura.registros(zip_, tmp_path / "a"))
    blocos = list(leitura.tabelas(zip_, tmp_path / "b", 3))

    assert [len(b) for b in blocos] == [3, 2, 2]
    novos = [(b.linhas[i], b.camadas[i], b.registro(i)) for b in blocos for i in range(len(b))]
    assert novos == [(r.linha, r.camada, r.atributos) for r in antigos]
    # o 0 continua inteiro (o GDAL entregaria 0.0): um campo INTEIRO não pode reprovar
    assert blocos[0].atributos["area"][1] == 0 and isinstance(blocos[0].atributos["area"][1], int)
    assert list(blocos[0].textos(["objectid", "falta"])["falta"]) == [None, None, None]


def test_coluna_que_aparece_depois_vira_nula_nas_anteriores(tmp_path):
    arquivo = tmp_path / "f.geojson"
    arquivo.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"a": 1}, "geometry": None},
        {"type": "Feature", "properties": {"a": 2, "b": "x"}, "geometry": None}]}))

    bloco = next(leitura.tabelas(arquivo, tmp_path / "x", 10))

    assert bloco.atributos == {"a": [1, 2], "b": [None, "x"]}


def test_levantamento_do_bloco_igual_ao_registro_a_registro(tmp_path):
    arquivo = tmp_path / "f.geojson"
    arquivo.write_bytes(_pagina(0, 4))
    um = colunas.Levantamento()
    for r in leitura.registros(arquivo, tmp_path / "a"):
        um.acrescentar(r.atributos, r.geometria)
    bloco = colunas.Levantamento()
    for t in leitura.tabelas(arquivo, tmp_path / "b", 3):
        bloco.acrescentar_tabela(t)

    assert (bloco.nomes, bloco.amostra, bloco.tem_geometria) == (um.nomes, um.amostra, um.tem_geometria)


def test_normalizar_tira_vertice_repetido_e_orienta():
    horario = box(-51, -25, -50, -24).exterior.coords[::-1]
    from shapely.geometry import Polygon
    normalizado = leitura.normalizar(Polygon(horario))
    assert normalizado.exterior.is_ccw
    assert leitura.normalizar(None) is None


# ------------------------------------------------------------------ desempate

def test_descartes_marca_a_ocorrencia_certa_de_cada_codigo():
    plano = {"A": SimpleNamespace(descartes={2: "perde"})}
    cols = SimpleNamespace(codigo="cod")

    assert generico._descartes(plano, cols, {"cod": ["A", "B", " A ", "A"]}) == {2: "perde"}
    assert generico._descartes({}, cols, {"cod": ["A"]}) == {}


# ------------------------------------------------------------------ Revalidar

def test_gravar_um_atualiza_a_chave_que_existe_e_insere_a_que_nao_existe():
    regras = [_regra("id", id_=1, campo="objectid", chave=True), _regra("v", id_=2, campo="valor")]
    banco = BancoFalso(COLUNAS)
    t = _tratada(banco, regras)
    destino = t.destinos[0]
    linha = t.linha(destino, {"id": "7", "v": "a"}, None)

    class Achou(CursorFalso):
        def fetchone(self):
            return ("rid7",)

    class NaoAchou(CursorFalso):
        def fetchone(self):
            return None

    assert t.gravar_um(Achou(banco), destino, linha) is False
    assert banco.instrucoes["UPDATE"][0][0]["rid"] == "rid7"
    assert t.gravar_um(NaoAchou(banco), destino, linha) is True
    assert len(banco.instrucoes["INSERT"]) == 1


def test_revalidar_nao_volta_o_dado_para_tras():
    regras = [_regra("id", id_=1, campo="objectid", chave=True)]
    banco = BancoFalso(COLUNAS)
    t = _tratada(banco, regras)
    destino = t.destinos[0]
    linha = t.linha(destino, {"id": "7"}, None)

    class Versao(CursorFalso):
        def __init__(self, banco_, maior):
            super().__init__(banco_)
            self.maior = maior

        def fetchone(self):
            return (self.maior,)

    with pytest.raises(revalidacao.Recusa, match="carga mais recente"):
        revalidacao._nao_voltar_para_tras(Versao(banco, 5), destino, linha, 3, "c")
    revalidacao._nao_voltar_para_tras(Versao(banco, 3), destino, linha, 3, "c")   # a mesma versão pode
    revalidacao._nao_voltar_para_tras(Versao(banco, None), destino, linha, 3, "c")
