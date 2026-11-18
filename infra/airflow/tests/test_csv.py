"""Leitura de CSV na carga genérica (US13.T22): separador, codificação, valores e geometria."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dags"))

from georural import colunas, gravacao, leitura  # noqa: E402


def _csv(tmp_path, texto: str, encoding: str = "utf-8", nome: str = "dados.csv") -> Path:
    caminho = tmp_path / nome
    caminho.write_bytes(texto.encode(encoding))
    return caminho


def _tudo(caminho, pasta, tamanho=10, geometria=True):
    return list(leitura.tabelas(caminho, pasta, tamanho, geometria=geometria))


# ------------------------------------------------------------------ separador e codificação

@pytest.mark.parametrize("separador", [";", ",", "\t", "|"])
def test_descobre_o_separador(tmp_path, separador):
    caminho = _csv(tmp_path, f"codigo{separador}nome\n1{separador}Ana\n2{separador}Bia\n")

    bloco = _tudo(caminho, tmp_path / "x")[0]

    assert bloco.atributos == {"codigo": ["1", "2"], "nome": ["Ana", "Bia"]}


def test_excel_em_portugues_latin1_com_ponto_e_virgula_e_decimal_com_virgula(tmp_path):
    caminho = _csv(tmp_path, "município;área\nSão Mateus do Sul;1.341,7\nCuritiba;434,9\n", "latin-1")

    bloco = _tudo(caminho, tmp_path / "x")[0]

    # O valor vai como texto: quem converte "434,9" é a regra DECIMAL com formato VIRGULA.
    assert bloco.atributos == {"município": ["São Mateus do Sul", "Curitiba"], "área": ["1.341,7", "434,9"]}


def test_utf8_com_bom_nao_suja_o_nome_da_primeira_coluna(tmp_path):
    caminho = _csv(tmp_path, "codigo,nome\n1,Ação\n", "utf-8-sig")

    assert list(_tudo(caminho, tmp_path / "x")[0].atributos) == ["codigo", "nome"]


def test_virgula_dentro_de_aspas_nao_quebra_a_coluna(tmp_path):
    caminho = _csv(tmp_path, 'codigo,descricao\n1,"Mata, ciliar"\n')

    assert _tudo(caminho, tmp_path / "x")[0].atributos["descricao"] == ["Mata, ciliar"]


# ------------------------------------------------------------------ valores e linhas

def test_vazio_vira_none_linha_curta_completa_e_linha_em_branco_some(tmp_path):
    caminho = _csv(tmp_path, "a;b;c\n1;;3\n4\n\n;;\n7;8;9;extra\n")

    bloco = _tudo(caminho, tmp_path / "x")[0]

    assert bloco.atributos == {"a": ["1", "4", "7"], "b": [None, None, "8"], "c": ["3", None, "9"]}
    assert bloco.linhas == [1, 2, 3]


def test_blocos_respeitam_o_tamanho_e_numeram_as_linhas(tmp_path):
    caminho = _csv(tmp_path, "n\n" + "".join(f"{i}\n" for i in range(7)))

    blocos = _tudo(caminho, tmp_path / "x", tamanho=3)

    assert [len(b) for b in blocos] == [3, 3, 1]
    assert blocos[-1].linhas == [7] and blocos[-1].atributos == {"n": ["6"]}


def test_tabelas_entrega_o_mesmo_que_registros(tmp_path):
    caminho = _csv(tmp_path, "id;wkt\n1;POINT (-51 -25)\n2;\n3;POINT (-50 -24)\n")

    antigos = list(leitura.registros(caminho, tmp_path / "a"))
    blocos = _tudo(caminho, tmp_path / "b", tamanho=2)

    novos = [(b.linhas[i], b.camadas[i], b.registro(i), b.geometrias[i]) for b in blocos for i in range(len(b))]
    assert novos == [(r.linha, r.camada, r.atributos, r.geometria) for r in antigos]


@pytest.mark.parametrize("texto, erro", [
    ("", "vazio"),
    ("a;;b\n1;2;3\n", "sem nome"),
    ("a;b;a\n1;2;3\n", "repetidas"),
])
def test_csv_invalido_rejeita_o_arquivo(tmp_path, texto, erro):
    with pytest.raises(leitura.ArquivoInvalido, match=erro):
        _tudo(_csv(tmp_path, texto), tmp_path / "x")


# ------------------------------------------------------------------ geometria

def test_coluna_wkt_vira_a_geometria_e_sai_dos_atributos(tmp_path):
    caminho = _csv(tmp_path, 'id;geometry\n1;"POLYGON ((-51 -25, -50 -25, -50 -24, -51 -25))"\n'
                             '2;"SRID=4326;POINT (-51 -25)"\n3;isso não é wkt\n4;\n')

    bloco = _tudo(caminho, tmp_path / "x")[0]

    assert list(bloco.atributos) == ["id"]
    assert bloco.geometrias[0].geom_type == "Polygon"
    assert bloco.geometrias[1].equals_exact(leitura.shapely.Point(-51, -25), 0)
    assert bloco.geometrias[2:] == [None, None]  # WKT quebrado e vazio: as regras reprovam se obrigatória


def test_latitude_longitude_viram_ponto_e_continuam_como_atributos(tmp_path):
    caminho = _csv(tmp_path, "foco;Latitude;Longitude\nA;-25,43;-49,27\nB;-25.5;-49.3\nC;123;-49\nD;;-49\n")

    bloco = _tudo(caminho, tmp_path / "x")[0]

    assert list(bloco.atributos) == ["foco", "Latitude", "Longitude"]
    assert (bloco.geometrias[0].x, bloco.geometrias[0].y) == (-49.27, -25.43)  # x = longitude
    assert (bloco.geometrias[1].x, bloco.geometrias[1].y) == (-49.3, -25.5)
    assert bloco.geometrias[2:] == [None, None]  # latitude fora da faixa e vazia


def test_wkt_tem_prioridade_e_x_y_nao_viram_geometria(tmp_path):
    com_wkt = _csv(tmp_path, "lat;lon;wkt\n-25;-49;POINT (-51 -24)\n", nome="a.csv")
    so_xy = _csv(tmp_path, "x;y\n500000;7180000\n", nome="b.csv")

    assert _tudo(com_wkt, tmp_path / "a")[0].geometrias[0].x == -51
    assert _tudo(so_xy, tmp_path / "b")[0].geometrias == [None]


def test_sem_geometria_na_primeira_passada_declara_pelo_cabecalho(tmp_path):
    com = _csv(tmp_path, "id;lat;lon\n1;-25;-49\n", nome="com.csv")
    sem = _csv(tmp_path, "id;nome\n1;Ana\n", nome="sem.csv")

    bloco_com = _tudo(com, tmp_path / "a", geometria=False)[0]
    bloco_sem = _tudo(sem, tmp_path / "b", geometria=False)[0]

    assert bloco_com.geometrias == [None] and bloco_com.geometria_declarada is True
    assert bloco_sem.geometria_declarada is False


@pytest.mark.parametrize("cabecalho, linha, tem", [
    ("id;wkt", "1;POINT (-51 -25)", True),
    ("id;latitude;longitude", "1;-25;-49", True),
    ("id;nome", "1;Ana", False),
])
def test_levantamento_igual_com_e_sem_geometria(tmp_path, cabecalho, linha, tem):
    caminho = _csv(tmp_path, f"{cabecalho}\n{linha}\n")
    com, sem = colunas.Levantamento(), colunas.Levantamento()
    for t in _tudo(caminho, tmp_path / "a"):
        com.acrescentar_tabela(t)
    for t in _tudo(caminho, tmp_path / "b", geometria=False):
        sem.acrescentar_tabela(t)

    assert (sem.nomes, sem.amostra, sem.tem_geometria) == (com.nomes, com.amostra, tem)


def test_csv_e_processavel():
    assert ".csv" in gravacao.EXTENSOES_PROCESSAVEIS
