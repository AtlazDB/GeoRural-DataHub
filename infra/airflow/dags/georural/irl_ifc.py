"""IRL e IFC em Python (US13.T10): lê do Oracle, calcula com o indicators.py e grava em
indicador_imovel, no lugar das funções calcular_irl e calcular_ifc do pkg_indicadores.

A escolha dos imóveis e das versões continua em SQL, como no PL/SQL (é só seleção de dados). As
contas são do indicators.py, e a contagem de focos é do Shapely (indicators.count_fires): o Oracle
só filtra pelo índice espacial (SDO_FILTER), como nas regras da carga.

A gravação é um MERGE com a mesma chave do PL/SQL (cod_imovel, indicador, regra, versão do
insumo, versão do imóvel), em lotes com commit por lote. Se cair no meio, rodar de novo completa.
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

import geopandas as gpd
import oracledb
import pandas as pd
import shapely

from georural import indicators
from georural.banco import conectar
from georural.config import Config

log = logging.getLogger(__name__)

SQL_RULE = """
    SELECT id, codigo, versao, unidade, parametros
      FROM regra_calculo
     WHERE indicador = :indicador AND ativa = 1
     ORDER BY versao DESC
     FETCH FIRST 1 ROWS ONLY"""

# Os mesmos imóveis e versões do calcular_irl: RL própria (soma e versão mais nova) ou, sem ela,
# município com alguma RL carregada (aí a versão é a do município e a RL é 0).
SQL_IRL = """
    SELECT i.cod_imovel, i.area_ha, i.dataset_versao_id, rl.area_rl,
           NVL(rl.versao_rl, cob.versao_rl)
      FROM imovel_rural i
      LEFT JOIN (SELECT cod_imovel, SUM(area_ha) AS area_rl, MAX(dataset_versao_id) AS versao_rl
                   FROM car_reserva_legal
                  GROUP BY cod_imovel) rl
        ON rl.cod_imovel = i.cod_imovel
      LEFT JOIN (SELECT SUBSTR(cod_imovel, 4, 7) AS cod_ibge, MAX(dataset_versao_id) AS versao_rl
                   FROM car_reserva_legal
                  WHERE REGEXP_LIKE(cod_imovel, '^[A-Z]{2}-[0-9]{7}-')
                  GROUP BY SUBSTR(cod_imovel, 4, 7)) cob
        ON cob.cod_ibge = SUBSTR(i.cod_imovel, 4, 7)
     WHERE i.area_ha > 0
       AND (rl.cod_imovel IS NOT NULL OR cob.cod_ibge IS NOT NULL)
       AND (:cod_imovel IS NULL OR i.cod_imovel = :cod_imovel)"""

SQL_FIRES_VERSION = "SELECT MAX(id) FROM dataset_versao WHERE conjunto = 'focos_calor'"

SQL_FIRES = """
    SELECT SDO_UTIL.TO_WKBGEOMETRY(geometria)
      FROM foco_calor
     WHERE satelite = :satelite AND data_hora >= :inicio AND data_hora < :fim"""

SQL_PROPERTIES = """
    SELECT cod_imovel, area_ha, dataset_versao_id
      FROM imovel_rural
     WHERE area_ha > 0
       AND (:cod_imovel IS NULL OR cod_imovel = :cod_imovel)"""

# Os focos guiam o índice espacial dos imóveis (como o hint ORDERED do pkg_indicadores).
SQL_CANDIDATES = """
    SELECT i.cod_imovel, SDO_UTIL.TO_WKBGEOMETRY(i.geometria)
      FROM imovel_rural i
     WHERE i.area_ha > 0
       AND (:cod_imovel IS NULL OR i.cod_imovel = :cod_imovel)
       AND i.ROWID IN (
           SELECT /*+ ORDERED */ c.ROWID
             FROM foco_calor fc
             JOIN imovel_rural c ON SDO_FILTER(c.geometria, fc.geometria) = 'TRUE'
            WHERE fc.satelite = :satelite AND fc.data_hora >= :inicio AND fc.data_hora < :fim)"""

SQL_MERGE = """
    MERGE INTO indicador_imovel d
    USING (SELECT :cod_imovel AS cod_imovel, :indicador AS indicador, :regra_id AS regra_calculo_id,
                  :versao AS dataset_versao_id, :versao_imovel AS versao_imovel_id
             FROM dual) s
       ON (d.cod_imovel = s.cod_imovel AND d.indicador = s.indicador
           AND d.regra_calculo_id = s.regra_calculo_id AND d.dataset_versao_id = s.dataset_versao_id
           AND d.versao_imovel_id = s.versao_imovel_id)
     WHEN MATCHED THEN UPDATE SET
          d.valor = :valor, d.status = :status, d.numerador = :numerador,
          d.area_imovel_ha = :area_imovel, d.area_ha = :saldo, d.unidade = :unidade,
          d.regra = :regra, d.calculado_em = SYSTIMESTAMP
     WHEN NOT MATCHED THEN INSERT
          (cod_imovel, indicador, regra_calculo_id, dataset_versao_id, versao_imovel_id,
           valor, status, numerador, area_imovel_ha, area_ha, unidade, regra, calculado_em)
          VALUES (s.cod_imovel, s.indicador, s.regra_calculo_id, s.dataset_versao_id, s.versao_imovel_id,
                  :valor, :status, :numerador, :area_imovel, :saldo, :unidade, :regra, SYSTIMESTAMP)"""


class RuleError(Exception):
    """Sem regra ativa ou sem parâmetro obrigatório (os erros -20010 e -20011 do PL/SQL)."""


def _exact_types(cursor, metadado):
    """NUMBER como Decimal (como no Oracle) e BLOB como bytes direto, sem um LOB por linha."""
    if metadado.type_code is oracledb.DB_TYPE_NUMBER:
        return cursor.var(Decimal, arraysize=cursor.arraysize)
    if metadado.type_code is oracledb.DB_TYPE_BLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_RAW, arraysize=cursor.arraysize)


def load_rule(cur, indicador: str) -> dict:
    """A regra vigente: ativa e de maior versão. parametros já vem como dict (coluna IS JSON)."""
    cur.execute(SQL_RULE, indicador=indicador)
    linha = cur.fetchone()
    if linha is None:
        raise RuleError(f"Nenhuma regra ativa para o indicador {indicador}")
    regra_id, codigo, versao, unidade, parametros = linha
    if isinstance(parametros, (str, bytes)):  # Oracle antes do 23: o JSON chega como texto
        parametros = json.loads(parametros, parse_float=Decimal)
    return {"id": int(regra_id), "codigo": codigo, "versao": int(versao), "unidade": unidade,
            "parametros": parametros}


def parameter(rule: dict, nome: str) -> Decimal:
    """Parâmetro numérico obrigatório da regra, em Decimal."""
    valor = rule["parametros"].get(nome)
    if valor is None:
        raise RuleError(f"A regra {rule['codigo']} v{rule['versao']} não tem o parâmetro {nome}")
    return Decimal(str(valor))


def _row(rule: dict, indicador: str, cod_imovel: str, versao: int, versao_imovel: int, valor: Decimal,
         status: str, numerador: Decimal, area_imovel: Decimal, saldo: Decimal | None) -> dict:
    return {
        "cod_imovel": cod_imovel, "indicador": indicador, "regra_id": rule["id"], "versao": int(versao),
        "versao_imovel": int(versao_imovel), "valor": valor, "status": status, "numerador": numerador,
        "area_imovel": area_imovel, "saldo": saldo, "unidade": rule["unidade"], "regra": rule["codigo"],
    }


def irl_rows(rule: dict, linhas: list[tuple]) -> list[dict]:
    """As linhas do IRL: (cod_imovel, area_ha, versao_imovel, area_rl ou None, versao_rl)."""
    minimo = parameter(rule, "percentual_minimo")
    rows = []
    for cod, area, versao_imovel, area_rl, versao_rl in linhas:
        irl = indicators.calculate_irl(area, area_rl, minimo)
        rows.append(_row(rule, "IRL", cod, versao_rl, versao_imovel, irl.value, irl.status,
                         irl.legal_reserve_ha, area, irl.balance_ha))
    return rows


def ifc_rows(rule: dict, properties: pd.DataFrame, counts: pd.Series, versao_focos: int) -> list[dict]:
    """As linhas do IFC, uma por imóvel com área: properties com cod_imovel, area_ha e
    versao_imovel_id; counts com os focos por cod_imovel (quem não aparece tem 0)."""
    por_ha = parameter(rule, "por_hectares")
    critico = parameter(rule, "limite_critico")
    rows = []
    for imovel in properties.itertuples(index=False):
        focos = int(counts.get(imovel.cod_imovel, 0))
        ifc = indicators.calculate_ifc(imovel.area_ha, focos, por_ha, critico)
        rows.append(_row(rule, "IFC", imovel.cod_imovel, versao_focos, imovel.versao_imovel_id,
                         ifc.value, ifc.status, Decimal(focos), imovel.area_ha, None))
    return rows


def save_rows(con, rows: list[dict], lote: int) -> int:
    """MERGE em indicador_imovel, com commit a cada lote. Devolve quantas linhas gravou."""
    with con.cursor() as cur:
        for inicio in range(0, len(rows), lote):
            cur.executemany(SQL_MERGE, rows[inicio:inicio + lote])
            con.commit()
    return len(rows)


def build_irl(con, cod_imovel: str | None = None) -> list[dict]:
    """As linhas do IRL, sem gravar (como o calcular_irl; cod_imovel limita a um imóvel)."""
    with con.cursor() as cur:
        cur.outputtypehandler = _exact_types
        rule = load_rule(cur, "IRL")
        cur.execute(SQL_IRL, cod_imovel=cod_imovel)
        return irl_rows(rule, cur.fetchall())


def build_ifc(con, cod_imovel: str | None = None, referencia: datetime | None = None) -> list[dict]:
    """As linhas do IFC, sem gravar (como o calcular_ifc). referencia é o fim da janela, em UTC
    sem fuso (padrão: agora), como o data_hora do foco_calor. Sem versão de focos, nada."""
    with con.cursor() as cur:
        cur.outputtypehandler = _exact_types
        rule = load_rule(cur, "IFC")
        meses = int(parameter(rule, "janela_meses"))
        satelite = rule["parametros"].get("satelite")
        cur.execute(SQL_FIRES_VERSION)
        versao_focos = cur.fetchone()[0]
        if versao_focos is None:
            return []
        fim = referencia or datetime.now(timezone.utc).replace(tzinfo=None)
        inicio = indicators.window_start(fim, meses)
        janela = {"satelite": satelite, "inicio": inicio, "fim": fim}

        cur.execute(SQL_FIRES, **janela)
        focos = gpd.GeoDataFrame(geometry=shapely.from_wkb([wkb for (wkb,) in cur.fetchall()]),
                                 crs="EPSG:4326")
        cur.execute(SQL_PROPERTIES, cod_imovel=cod_imovel)
        properties = pd.DataFrame(cur.fetchall(), columns=["cod_imovel", "area_ha", "versao_imovel_id"])
        cur.execute(SQL_CANDIDATES, cod_imovel=cod_imovel, **janela)
        candidatos = cur.fetchall()
        candidates = gpd.GeoDataFrame({"cod_imovel": [cod for cod, _ in candidatos]},
                                      geometry=shapely.from_wkb([wkb for _, wkb in candidatos]),
                                      crs="EPSG:4326")

    counts = indicators.count_fires(candidates, focos)
    return ifc_rows(rule, properties, counts, int(versao_focos))


def calculate_on(con, indicadores: tuple[str, ...], lote: int, cod_imovel: str | None = None,
                 referencia: datetime | None = None) -> dict:
    """Calcula e grava os indicadores pedidos numa conexão já aberta (a carga do generico.py
    usa a dela). Devolve quantos imóveis cada um gravou."""
    resultado = {}
    if "IRL" in indicadores:
        resultado["IRL"] = save_rows(con, build_irl(con, cod_imovel), lote)
    if "IFC" in indicadores:
        resultado["IFC"] = save_rows(con, build_ifc(con, cod_imovel, referencia), lote)
    log.info("Indicadores gravados: %s", resultado)
    return resultado


def calculate(cfg: Config, indicadores: tuple[str, ...] = ("IRL", "IFC"), cod_imovel: str | None = None,
              referencia: datetime | None = None) -> dict:
    """Calcula os indicadores pedidos e devolve quantos imóveis cada um gravou (para o XCom)."""
    with conectar(cfg) as con:
        return calculate_on(con, indicadores, cfg.tamanho_lote, cod_imovel, referencia)
