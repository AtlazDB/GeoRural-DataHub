"""Indicadores cadastrados pelo usuário (US13.T06 e T07): lê o indicator_definition, cruza as feições
da fonte (input_feature) com cada imóvel, calcula com o indicators.py e grava em indicator_result.

Dois modelos:
- POINT_DENSITY (T06): pontos da fonte que tocam o imóvel ÷ área declarada (imovel_rural.area_ha)
  × fator, como o IFC.
- AREA_COVERAGE (T07): área do imóvel coberta pelos polígonos da fonte ÷ área do contorno × 100. Os
  dois lados são medidos do mesmo jeito (área geodésica do polígono), então o valor fica entre 0 e 100.

Filtro opcional nos dois: parameters.category, que compara com input_feature.categoria (ex.: só os
focos de um satélite). O valor vai como parâmetro do SQL, nunca concatenado.

Imóvel sem nenhuma feição também ganha resultado (valor 0), para a tela mostrar o status dele.

Só a geometria dos imóveis que podem tocar a fonte sai do banco: o SDO_FILTER (índice espacial, só o
retângulo) separa os candidatos, e quem decide é o Shapely, como nas regras da carga.

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
AREA_COVERAGE = "AREA_COVERAGE"
# get_gtype() do SDO_GEOMETRY: 1 = ponto, 5 = multiponto, 3 = polígono, 7 = multipolígono
_TIPOS = {POINT_DENSITY: ({1, 5}, "pontos"), AREA_COVERAGE: ({3, 7}, "polígonos")}

SQL_DEFINITION = """
    SELECT fonte_id, calculation_model, parameters, ranges, active
      FROM indicator_definition
     WHERE code = :code"""

# A fonte de cada linha vem pela versão: dataset_versao -> execucao -> arquivo_bruto.
# Sem categoria (NULL), o filtro não corta nada.
_DA_FONTE = """
      JOIN dataset_versao dv ON dv.id = f.dataset_versao_id
      JOIN execucao e        ON e.id  = dv.execucao_id
      JOIN arquivo_bruto a   ON a.id  = e.arquivo_bruto_id
     WHERE a.fonte_id = :fonte
       AND (:categoria IS NULL OR f.categoria = :categoria)"""

SQL_FEATURES = """
    SELECT f.dataset_versao_id, f.geometria.get_gtype(), SDO_UTIL.TO_WKBGEOMETRY(f.geometria)
      FROM input_feature f""" + _DA_FONTE

SQL_PROPERTIES = """
    SELECT cod_imovel, area_ha, dataset_versao_id
      FROM imovel_rural
     WHERE area_ha > 0"""

# As feições guiam o índice espacial dos imóveis (como o hint ORDERED do pkg_indicadores).
SQL_CANDIDATES = """
    SELECT i.cod_imovel, SDO_UTIL.TO_WKBGEOMETRY(i.geometria)
      FROM imovel_rural i
     WHERE i.area_ha > 0
       AND i.ROWID IN (
           SELECT /*+ ORDERED */ c.ROWID
             FROM input_feature f
             JOIN imovel_rural c ON SDO_FILTER(c.geometria, f.geometria) = 'TRUE'""" + _DA_FONTE + ")"

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
    """O indicador não pode ser calculado (não existe, está desativado, fonte sem dados...). A
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
    if model not in _TIPOS:
        raise IndicatorError(f"Modelo de cálculo desconhecido: {model}.")
    parameters = _from_json(parameters) or {}
    return {
        "code": code,
        "fonte_id": int(fonte_id),
        "model": model,
        "parameters": parameters,
        "category": parameters.get("category") or None,
        "ranges": _from_json(ranges),
    }


def load_features(cur, fonte_id: int, model: str, category: str | None = None) -> tuple[gpd.GeoDataFrame, int]:
    """As feições da fonte (só as da categoria, se houver) e a versão usada (a mais nova entre as
    linhas). O tipo tem que combinar com o modelo: pontos na densidade, polígonos na cobertura."""
    cur.execute(SQL_FEATURES, fonte=fonte_id, categoria=category)
    linhas = cur.fetchall()
    if not linhas:
        filtro = f" com a categoria {category!r}" if category else ""
        raise IndicatorError(f"A fonte {fonte_id} não tem dados{filtro} no insumo genérico (input_feature).")
    esperados, nome = _TIPOS[model]
    tipos = {int(gtype) for _, gtype, _ in linhas}
    if not tipos <= esperados:
        raise IndicatorError(f"{model} precisa de {nome}, e a fonte {fonte_id} tem outros tipos ({sorted(tipos)}).")
    versao = max(int(versao) for versao, _, _ in linhas)
    feicoes = gpd.GeoDataFrame(geometry=shapely.from_wkb([wkb for _, _, wkb in linhas]), crs="EPSG:4326")
    return feicoes, versao


def load_properties(cur) -> pd.DataFrame:
    """Todos os imóveis com área declarada, sem geometria: cod_imovel, area_ha, versao_imovel_id."""
    cur.execute(SQL_PROPERTIES)
    return pd.DataFrame(cur.fetchall(), columns=["cod_imovel", "area_ha", "versao_imovel_id"])


def load_candidates(cur, fonte_id: int, category: str | None = None) -> gpd.GeoDataFrame:
    """A geometria só dos imóveis que podem tocar a fonte (o retângulo encosta numa feição)."""
    cur.execute(SQL_CANDIDATES, fonte=fonte_id, categoria=category)
    linhas = cur.fetchall()
    return gpd.GeoDataFrame(
        {"cod_imovel": [cod for cod, _ in linhas]},
        geometry=shapely.from_wkb([wkb for _, wkb in linhas]),
        crs="EPSG:4326",
    )


def _row(code, imovel, versao, value, status, numerator, area) -> dict:
    return {
        "code": code,
        "cod_imovel": imovel.cod_imovel,
        "versao": versao,
        "versao_imovel": int(imovel.versao_imovel_id),
        "value": value,
        "status": status,
        "numerator": numerator,
        "area": area,
    }


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
        rows.append(_row(code, imovel, points_version, result.value, result.status, Decimal(points), imovel.area_ha))
    return rows


def area_coverage_rows(code: str, properties: pd.DataFrame, coverage: pd.DataFrame, version: int,
                       ranges: list[dict]) -> list[dict]:
    """Uma linha de indicator_result por imóvel, pronta para o MERGE.

    coverage: cod_imovel, property_ha e covered_ha dos imóveis que encostam na fonte
    (indicators.coverage_by_property). Quem não aparece fica com 0% e área do contorno vazia: ela só
    é medida para quem encosta, para não trazer a geometria de todos os imóveis do banco.
    """
    medidos = {c.cod_imovel: c for c in coverage.itertuples(index=False)}
    rows = []
    for imovel in properties.itertuples(index=False):
        medido = medidos.get(imovel.cod_imovel)
        if medido is None:
            value = Decimal("0.0000")
            rows.append(_row(code, imovel, version, value, indicators.status_from_ranges(value, ranges),
                             Decimal("0.0000"), None))
            continue
        result = indicators.calculate_area_coverage(medido.property_ha, medido.covered_ha, ranges)
        rows.append(_row(code, imovel, version, result.value, result.status, result.covered_ha,
                         result.property_area_ha))
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
            fonte, model, category = definition["fonte_id"], definition["model"], definition["category"]
            feicoes, versao = load_features(cur, fonte, model, category)
            properties = load_properties(cur)
            candidates = load_candidates(cur, fonte, category)

        if model == POINT_DENSITY:
            counts = indicators.count_fires(candidates, feicoes)
            factor = Decimal(str(definition["parameters"].get("factor", 1)))
            rows = point_density_rows(code, properties, counts, versao, factor, definition["ranges"])
            tocados = int((counts > 0).sum())
        else:
            coverage = indicators.coverage_by_property(candidates, feicoes)
            rows = area_coverage_rows(code, properties, coverage, versao, definition["ranges"])
            tocados = int((coverage["covered_ha"] > 0).sum())
        gravados = save_rows(con, rows, cfg.tamanho_lote)

    log.info("%s (%s%s): %d imóveis gravados (%d tocados pela fonte), versão da fonte %d", code, model,
             f", categoria {category!r}" if category else "", gravados, tocados, versao)
    return {"code": code, "model": model, "category": category, "imoveis": gravados,
            "tocados": tocados, "versao_fonte": versao}
