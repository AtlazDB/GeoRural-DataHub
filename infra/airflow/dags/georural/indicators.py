"""Cálculo dos indicadores IRL e IFC em Python (US13.T10), no lugar do pkg_indicadores (PL/SQL).

Este módulo só tem as contas, sem banco: recebe os números já lidos e devolve o valor e o
status de cada imóvel. A leitura e a gravação no Oracle ficam em outro passo.

Os resultados precisam bater com os do PL/SQL, que são a referência
(docs/memoria-de-calculo no back):
  - tudo em Decimal, nunca float, como o NUMBER do Oracle;
  - ROUND(x, 4) do Oracle arredonda o 0,5 para longe do zero, e não para o par como o round();
  - o status compara o valor já arredondado.
"""

import calendar
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import Geod

FOUR_PLACES = Decimal("0.0001")
# NUMBER do Oracle guarda até 38 dígitos; o Decimal padrão do Python, 28.
ORACLE_PRECISION = 38

CONFORME = "CONFORME"
ALERTA = "ALERTA"
CRITICO = "CRITICO"


def round4(value: Decimal) -> Decimal:
    """ROUND(value, 4) do Oracle: o 0,5 vai para longe do zero (2,00005 -> 2,0001)."""
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class IrlResult:
    value: Decimal             # % de RL no imóvel
    status: str
    legal_reserve_ha: Decimal  # numerador: RL declarada, com teto na área do imóvel
    property_area_ha: Decimal  # denominador
    balance_ha: Decimal        # saldo: + excedente, - déficit em relação ao mínimo


def calculate_irl(property_area_ha: Decimal, legal_reserve_ha: Decimal | None,
                  minimum_pct: Decimal) -> IrlResult:
    """IRL de um imóvel, como o pkg_indicadores.calcular_irl.

    Usa a área declarada (area_ha), não a área do polígono. Sem RL declarada conta como 0.
    A RL entra com teto na área do imóvel: fragmentos sobrepostos não passam de 100%.
    """
    with localcontext() as ctx:
        ctx.prec = ORACLE_PRECISION
        reserve = min(legal_reserve_ha or Decimal(0), property_area_ha)
        value = round4(reserve / property_area_ha * 100)
        balance = round4(reserve - property_area_ha * minimum_pct / 100)
    status = CONFORME if value >= minimum_pct else CRITICO
    return IrlResult(value, status, reserve, property_area_ha, balance)


def window_start(end: datetime, months: int) -> datetime:
    """ADD_MONTHS(end, -months) do Oracle: o início da janela do IFC.

    Se end é o último dia do mês, o resultado também é (30/04 - 1 mês = 31/03); se o dia não
    existe no mês de destino, vai para o último (31/03 - 1 mês = 28/02). ADD_MONTHS devolve
    DATE, que não tem fração de segundo.
    """
    total = end.year * 12 + (end.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    end_is_last_day = end.day == calendar.monthrange(end.year, end.month)[1]
    day = last_day if end_is_last_day else min(end.day, last_day)
    return end.replace(year=year, month=month, day=day, microsecond=0)


@dataclass(frozen=True)
class IfcResult:
    value: Decimal             # focos por por_hectares ha
    status: str
    fires: int                 # numerador: focos dentro do imóvel na janela
    property_area_ha: Decimal  # denominador


def calculate_ifc(property_area_ha: Decimal, fires: int, per_hectares: Decimal,
                  critical_limit: Decimal) -> IfcResult:
    """IFC de um imóvel, como o pkg_indicadores.calcular_ifc (a contagem dos focos vem pronta).

    Nenhum foco é CONFORME; a partir do limite_critico é CRITICO; entre os dois, ALERTA.
    """
    with localcontext() as ctx:
        ctx.prec = ORACLE_PRECISION
        value = round4(Decimal(fires) / property_area_ha * per_hectares)
    if fires == 0:
        status = CONFORME
    elif value >= critical_limit:
        status = CRITICO
    else:
        status = ALERTA
    return IfcResult(value, status, fires, property_area_ha)


def count_fires(properties: gpd.GeoDataFrame, fires: gpd.GeoDataFrame) -> pd.Series:
    """Focos que tocam cada imóvel, como o SDO_RELATE ANYINTERACT do pkg_indicadores.

    properties: cod_imovel + geometry. fires: geometry (já filtrados por satélite e janela).
    Devolve uma contagem por cod_imovel, com 0 para quem não tem foco. A borda conta, e um foco
    na divisa conta para os dois imóveis. Diferença conhecida: o Oracle usa tolerância de 5 cm e
    conta um foco a menos de 5 cm do lado de fora; aqui não conta (na conferência de Bituruna,
    1993 de 1993 imóveis bateram).
    """
    joined = gpd.sjoin(fires, properties[["cod_imovel", "geometry"]], predicate="intersects", how="inner")
    counts = joined.groupby("cod_imovel").size()
    return counts.reindex(properties["cod_imovel"], fill_value=0)


def status_from_ranges(value: Decimal, ranges: list[dict]) -> str:
    """Status de um indicador cadastrado pelas faixas dele (indicator_definition.ranges).

    As faixas vêm em ordem: [{status, limit, includeLimit}, ..., {status}]. Vale a primeira em
    que o valor cabe: até o limite com includeLimit (<=), abaixo dele sem (<). A última não tem
    limite e pega o resto. O back (US13.T04) garante a ordem e a última sem limite.
    """
    for faixa in ranges:
        limit = faixa.get("limit")
        if limit is None:
            return faixa["status"]
        limit = Decimal(str(limit))
        if value < limit or (faixa.get("includeLimit") and value == limit):
            return faixa["status"]
    raise ValueError(f"Faixas sem a última sem limite: {ranges}")


@dataclass(frozen=True)
class PointDensityResult:
    value: Decimal             # pontos por factor ha
    status: str
    points: int                # numerador: pontos da fonte que tocam o imóvel
    property_area_ha: Decimal  # denominador


def calculate_point_density(property_area_ha: Decimal, points: int, factor: Decimal,
                            ranges: list[dict]) -> PointDensityResult:
    """POINT_DENSITY de um imóvel: pontos ÷ área declarada × fator, com o status pelas faixas.

    A conta é a mesma do IFC, mas o status vem das faixas que o usuário cadastrou.
    """
    with localcontext() as ctx:
        ctx.prec = ORACLE_PRECISION
        value = round4(Decimal(points) / property_area_ha * factor)
    return PointDensityResult(value, status_from_ranges(value, ranges), points, property_area_ha)


# Área geodésica no elipsoide do SIRGAS 2000 (como o regras.area_m2 e o SDO_AREA no SRID 4326).
_GEOD = Geod(ellps="GRS80")


def area_ha(geometria) -> Decimal:
    """Área geodésica da parte poligonal da geometria, em hectares e 4 casas. Linhas e pontos que
    sobram de uma interseção (imóveis que só encostam) não têm área e contam 0."""
    poligonos = [p for p in shapely.get_parts(geometria) if p.geom_type in ("Polygon", "MultiPolygon")]
    m2 = sum(abs(_GEOD.geometry_area_perimeter(p)[0]) for p in poligonos)
    return round4(Decimal(str(m2)) / 10000)


def _poligonal(geometria):
    """Só a parte de área da geometria (Polygon/MultiPolygon). Linhas e pontos viram nada."""
    if geometria.geom_type in ("Polygon", "MultiPolygon"):
        return geometria
    partes = [p for p in shapely.get_parts(geometria) if p.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(partes) if partes else shapely.Polygon()


def _validas(geometrias):
    """make_valid + só a parte de área. O make_valid de um polígono inválido pode devolver uma
    GeometryCollection com polígonos e linhas; as linhas não têm área e só atrapalham a união."""
    return np.array([_poligonal(g) for g in np.atleast_1d(shapely.make_valid(geometrias))], dtype=object)


# Grade de ~0,1 mm (em graus) em que o GEOS arredonda os vértices na união e na interseção.
_GRADE_GRAUS = 1e-9


def _intersecao_com_uniao(alvo, partes):
    """alvo ∩ união(partes), sempre na grade fixa (snap rounding do GEOS), que é a sobreposição
    robusta. Sem a grade, camadas com bordas quase iguais (APP e vegetação do CAR) ou falhavam com
    "non-noded intersection" (TD2VA, 11/11) ou, pior, davam área errada SEM erro: 48,49 ha em vez
    de 50,23 num imóvel de São Mateus do Sul (12/11). A grade muda a área bem abaixo das 4 casas."""
    return shapely.intersection(alvo, shapely.union_all(partes, grid_size=_GRADE_GRAUS),
                                grid_size=_GRADE_GRAUS)


def coverage_by_property(properties: gpd.GeoDataFrame, features: gpd.GeoDataFrame) -> pd.DataFrame:
    """Área do contorno e área coberta pela fonte em cada imóvel que encosta em alguma feição.

    properties: cod_imovel + geometry. features: geometry (polígonos da fonte, já filtrados).
    A cobertura é a interseção do imóvel com a UNIÃO das feições que o tocam: duas feições
    sobrepostas não contam a mesma área duas vezes. Geometria inválida passa pelo make_valid
    antes (a interseção do GEOS falha com anel que se cruza). Imóvel sem feição não aparece.
    Devolve cod_imovel, property_ha e covered_ha.
    """
    pares = gpd.sjoin(properties[["cod_imovel", "geometry"]], features[["geometry"]],
                      predicate="intersects", how="inner")
    linhas = []
    for cod, grupo in pares.groupby("cod_imovel"):
        imovel = _validas(grupo.geometry.iloc[0])[0]
        feicoes = _validas(features.geometry.loc[grupo["index_right"]].values)
        coberta = _intersecao_com_uniao(imovel, feicoes)
        imovel_ha = area_ha(imovel)
        # A interseção medida pode passar 1–2 m² do contorno (arredondamento): no máximo 100%.
        linhas.append((cod, imovel_ha, min(area_ha(coberta), imovel_ha)))
    return pd.DataFrame(linhas, columns=["cod_imovel", "property_ha", "covered_ha"])


def base_coverage_by_property(properties: gpd.GeoDataFrame, base: gpd.GeoDataFrame,
                              cover: gpd.GeoDataFrame) -> pd.DataFrame:
    """Para cada imóvel que encosta na camada base (ex.: APP): a área da base dentro do imóvel e
    quanto dela está coberto pela outra camada (ex.: vegetação nativa). É o IAPP.

    properties: cod_imovel + geometry. base e cover: geometry (polígonos, já filtrados).
    base_ha = imóvel ∩ união da base; covered_ha = (imóvel ∩ base) ∩ união da cobertura. Sobreposição
    dentro de uma camada não conta duas vezes. Imóvel cuja base dentro dele não tem área (só
    encosta na borda) não aparece: sem APP não há o que medir.
    Devolve cod_imovel, base_ha e covered_ha.
    """
    pares = gpd.sjoin(properties[["cod_imovel", "geometry"]], base[["geometry"]],
                      predicate="intersects", how="inner")
    cobertura = _validas(cover.geometry.values) if len(cover) else None
    indice = shapely.STRtree(cobertura) if cobertura is not None else None
    linhas = []
    for cod, grupo in pares.groupby("cod_imovel"):
        imovel = _validas(grupo.geometry.iloc[0])[0]
        partes_base = _validas(base.geometry.loc[grupo["index_right"]].values)
        base_no_imovel = _poligonal(_intersecao_com_uniao(imovel, partes_base))
        base_ha = area_ha(base_no_imovel)
        if base_ha <= 0:
            continue
        coberta_ha = Decimal("0.0000")
        if indice is not None:
            tocam = indice.query(base_no_imovel, predicate="intersects")
            if len(tocam):
                # No máximo a base inteira (arredondamento da medida): no máximo 100%.
                coberta_ha = min(area_ha(_intersecao_com_uniao(base_no_imovel, cobertura[tocam])), base_ha)
        linhas.append((cod, base_ha, coberta_ha))
    return pd.DataFrame(linhas, columns=["cod_imovel", "base_ha", "covered_ha"])


@dataclass(frozen=True)
class AreaCoverageResult:
    value: Decimal             # % da área do imóvel coberta pela fonte
    status: str
    covered_ha: Decimal        # numerador: área da interseção com a fonte (ha)
    property_area_ha: Decimal  # denominador: área do contorno do imóvel (ha), medida como o numerador


def calculate_area_coverage(property_area_ha: Decimal, covered_ha: Decimal, ranges: list[dict]) -> AreaCoverageResult:
    """AREA_COVERAGE de um imóvel: área coberta ÷ área do contorno × 100, com o status pelas faixas.

    Os dois lados são medidos do mesmo jeito (área geodésica do polígono), então o valor fica
    entre 0 e 100. Não usa a área declarada: ela não é a área que a interseção mede.
    """
    with localcontext() as ctx:
        ctx.prec = ORACLE_PRECISION
        value = round4(covered_ha / property_area_ha * 100) if property_area_ha > 0 else Decimal("0.0000")
    return AreaCoverageResult(value, status_from_ranges(value, ranges), covered_ha, property_area_ha)
