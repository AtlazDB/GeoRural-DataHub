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
import pandas as pd

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
