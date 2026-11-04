"""Indicadores cadastrados pelo usuário (US13.T06): lê o indicator_definition, conta os pontos
da fonte (input_feature) em cada imóvel, calcula com o indicators.py e grava em indicator_result.

Os pontos vêm da fonte que o usuário escolheu no cadastro. O denominador é a área declarada do
imóvel (imovel_rural.area_ha), como no IRL e no IFC. Imóvel sem ponto também ganha resultado
(valor 0), para a tela mostrar o status dele.

Só a geometria dos imóveis que podem ter ponto sai do banco: o SDO_FILTER (índice espacial, só o
retângulo) separa os candidatos, e quem decide se o ponto toca o imóvel é o Shapely
(indicators.count_fires), como nas regras da carga. Os outros imóveis entram com 0.

A gravação é em lotes, com commit por lote (o undo do Autonomous é limitado). Se a execução cair
no meio, rodar de novo completa: o MERGE não duplica.
"""

import json
import logging
from decimal import Decimal

import geopandas as gpd
import oracledb
import pandas as pd
import shapely

from georural import indicators
from georural.banco import conectar
from georural.config import Config

log = logging.getLogger(__name__)

POINT_DENSITY = "POINT_DENSITY"
# get_gtype() do SDO_GEOMETRY: 1 = ponto, 5 = multiponto
_POINT_TYPES = {1, 5}

SQL_DEFINITION = """
    SELECT fonte_id, calculation_model, parameters, ranges, active
      FROM indicator_definition
     WHERE code = :code"""

# A fonte de cada linha vem pela versão: dataset_versao -> execucao -> arquivo_bruto.
SQL_POINTS = """
    SELECT f.dataset_versao_id, f.geometria.get_gtype(), SDO_UTIL.TO_WKBGEOMETRY(f.geometria)
      FROM input_feature f
      JOIN dataset_versao dv ON dv.id = f.dataset_versao_id
      JOIN execucao e        ON e.id  = dv.execucao_id
      JOIN arquivo_bruto a   ON a.id  = e.arquivo_bruto_id
     WHERE a.fonte_id = :fonte"""

SQL_PROPERTIES = """
    SELECT cod_imovel, area_ha, dataset_versao_id
      FROM imovel_rural
     WHERE area_ha > 0"""

# Os pontos guiam o índice espacial dos imóveis (como o hint ORDERED do pkg_indicadores).
SQL_CANDIDATES = """
    SELECT i.cod_imovel, SDO_UTIL.TO_WKBGEOMETRY(i.geometria)
      FROM imovel_rural i
     WHERE i.area_ha > 0
       AND i.ROWID IN (
           SELECT /*+ ORDERED */ c.ROWID
             FROM input_feature f
             JOIN dataset_versao dv ON dv.id = f.dataset_versao_id
             JOIN execucao e        ON e.id  = dv.execucao_id
             JOIN arquivo_bruto a   ON a.id  = e.arquivo_bruto_id
             JOIN imovel_rural c    ON SDO_FILTER(c.geometria, f.geometria) = 'TRUE'
            WHERE a.fonte_id = :fonte)"""

SQL_MERGE = """
    MERGE INTO indicator_result r
    USING (SELECT :code AS indicator_code, :cod_imovel AS cod_imovel, :versao AS dataset_versao_id,
                  :versao_imovel AS versao_imovel_id
             FROM dual) s
       ON (r.indicator_code = s.indicator_code AND r.cod_imovel = s.cod_imovel
           AND r.dataset_versao_id = s.dataset_versao_id AND r.versao_imovel_id = s.versao_imovel_id)
     WHEN MATCHED THEN UPDATE SET
          r.value = :value, r.status = :status, r.numerator = :numerator,
          r.area_imovel_ha = :area, r.calculated_at = SYSTIMESTAMP
     WHEN NOT MATCHED THEN INSERT
          (indicator_code, cod_imovel, dataset_versao_id, versao_imovel_id,
           value, status, numerator, area_imovel_ha)
          VALUES (s.indicator_code, s.cod_imovel, s.dataset_versao_id, s.versao_imovel_id,
                  :value, :status, :numerator, :area)"""


class IndicatorError(Exception):
    """O indicador não pode ser calculado (não existe, está desativado, fonte sem pontos...). A
    mensagem vai para o log da DAG."""


def _exact_types(cursor, metadado):
    """NUMBER como Decimal (como no Oracle) e BLOB como bytes direto, sem um LOB por linha."""
    if metadado.type_code is oracledb.DB_TYPE_NUMBER:
        return cursor.var(Decimal, arraysize=cursor.arraysize)
    if metadado.type_code is oracledb.DB_TYPE_BLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_RAW, arraysize=cursor.arraysize)


def _from_json(value):
    """Coluna com CHECK (... IS JSON): o Oracle 23 entrega o JSON já convertido (dict/list) ao
    python-oracledb; versões antigas entregam o texto. Números do texto viram Decimal."""
    if isinstance(value, (str, bytes)):
        return json.loads(value, parse_float=Decimal)
    return value


def load_definition(cur, code: str) -> dict:
    """O indicador cadastrado, com parameters e ranges já convertidos do JSON (números em Decimal)."""
    cur.execute(SQL_DEFINITION, code=code)
    linha = cur.fetchone()
    if linha is None:
        raise IndicatorError(f"Indicador não encontrado: {code}")
    fonte_id, model, parameters, ranges, active = linha
    if not active:
        raise IndicatorError(f"O indicador {code} está desativado.")
    if model != POINT_DENSITY:
        raise IndicatorError(f"Modelo {model} ainda não é calculado (só {POINT_DENSITY}).")
    return {
        "code": code,
        "fonte_id": int(fonte_id),
        "model": model,
        "parameters": _from_json(parameters),
        "ranges": _from_json(ranges),
    }


def load_points(cur, fonte_id: int) -> tuple[gpd.GeoDataFrame, int]:
    """Os pontos da fonte e a versão usada (a mais nova entre as linhas da fonte)."""
    cur.execute(SQL_POINTS, fonte=fonte_id)
    linhas = cur.fetchall()
    if not linhas:
        raise IndicatorError(f"A fonte {fonte_id} não tem dados no insumo genérico (input_feature).")
    tipos = {int(gtype) for _, gtype, _ in linhas}
    if not tipos <= _POINT_TYPES:
        raise IndicatorError(f"A fonte {fonte_id} tem geometrias que não são pontos (tipos {sorted(tipos)}).")
    versao = max(int(versao) for versao, _, _ in linhas)
    pontos = gpd.GeoDataFrame(geometry=shapely.from_wkb([wkb for _, _, wkb in linhas]), crs="EPSG:4326")
    return pontos, versao


def load_properties(cur) -> pd.DataFrame:
    """Todos os imóveis com área declarada, sem geometria: cod_imovel, area_ha, versao_imovel_id."""
    cur.execute(SQL_PROPERTIES)
    return pd.DataFrame(cur.fetchall(), columns=["cod_imovel", "area_ha", "versao_imovel_id"])


def load_candidates(cur, fonte_id: int) -> gpd.GeoDataFrame:
    """A geometria só dos imóveis que podem ter ponto da fonte (retângulo encosta num ponto)."""
    cur.execute(SQL_CANDIDATES, fonte=fonte_id)
    linhas = cur.fetchall()
    return gpd.GeoDataFrame(
        {"cod_imovel": [cod for cod, _ in linhas]},
        geometry=shapely.from_wkb([wkb for _, wkb in linhas]),
        crs="EPSG:4326",
    )


def point_density_rows(code: str, properties: pd.DataFrame, counts: pd.Series, points_version: int,
                       factor: Decimal, ranges: list[dict]) -> list[dict]:
    """Uma linha de indicator_result por imóvel, pronta para o MERGE.

    properties: cod_imovel, area_ha (Decimal, > 0) e versao_imovel_id de cada imóvel.
    counts: pontos por cod_imovel; quem não aparece tem 0.
    points_version: a versão da fonte (dataset_versao) usada no cálculo.
    """
    rows = []
    for imovel in properties.itertuples(index=False):
        points = int(counts.get(imovel.cod_imovel, 0))
        result = indicators.calculate_point_density(imovel.area_ha, points, factor, ranges)
        rows.append({
            "code": code,
            "cod_imovel": imovel.cod_imovel,
            "versao": points_version,
            "versao_imovel": int(imovel.versao_imovel_id),
            "value": result.value,
            "status": result.status,
            "numerator": Decimal(points),
            "area": imovel.area_ha,
        })
    return rows


def save_rows(con, rows: list[dict], lote: int) -> int:
    """MERGE em indicator_result, com commit a cada lote. Devolve quantas linhas gravou."""
    with con.cursor() as cur:
        for inicio in range(0, len(rows), lote):
            cur.executemany(SQL_MERGE, rows[inicio:inicio + lote])
            con.commit()
    return len(rows)


def calculate(cfg: Config, code: str) -> dict:
    """Calcula um indicador cadastrado para todos os imóveis e grava. Devolve um resumo para o XCom."""
    with conectar(cfg) as con:
        with con.cursor() as cur:
            cur.outputtypehandler = _exact_types
            definition = load_definition(cur, code)
            pontos, versao = load_points(cur, definition["fonte_id"])
            properties = load_properties(cur)
            candidates = load_candidates(cur, definition["fonte_id"])

        counts = indicators.count_fires(candidates, pontos)
        factor = Decimal(str(definition["parameters"].get("factor", 1)))
        rows = point_density_rows(code, properties, counts, versao, factor, definition["ranges"])
        gravados = save_rows(con, rows, cfg.tamanho_lote)

    com_pontos = int((counts > 0).sum())
    log.info("%s: %d imóveis gravados (%d com ponto), versão da fonte %d", code, gravados, com_pontos, versao)
    return {"code": code, "imoveis": gravados, "com_pontos": com_pontos, "versao_fonte": versao}
