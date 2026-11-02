"""Conversão de geometrias do shapely para SDO_GEOMETRY do Oracle (polígono, ponto e linha).

Por que não mandar WKB e converter no banco (como o Java faz com SDO_GEOMETRY(blob, srid)):
o python-oracledb em modo thin envia bytes como RAW, que tem limite de 32 KB, e os imóveis
grandes do CAR passam disso. Montar o objeto aqui não tem limite de tamanho e é rápido
(~2 s para 5 mil polígonos, testado contra o Oracle 23ai).

O sentido dos anéis tem que estar certo antes (anel externo anti-horário, buracos horário):
validacao.py já faz isso com shapely.orient_polygons.
"""

import shapely
from shapely.geometry.base import BaseGeometry

SRID_BANCO = 4326

GTYPE_PONTO = 2001          # 2 = duas dimensões, 001 = ponto
GTYPE_LINHA = 2002
GTYPE_POLIGONO = 2003       # 003 = polígono
GTYPE_MULTIPONTO = 2005
GTYPE_MULTILINHA = 2006
GTYPE_MULTIPOLIGONO = 2007  # 007 = multipolígono
ETYPE_PONTO = 1
ETYPE_LINHA = 2
ETYPE_ANEL_EXTERNO = 1003
ETYPE_BURACO = 2003
INTERPRETACAO_RETAS = 1     # vértices ligados por segmentos de reta


def elementos(geometria: BaseGeometry) -> tuple[int, list[int], list[float]]:
    """(SDO_GTYPE, SDO_ELEM_INFO, SDO_ORDINATES) de um polígono, linha ou multiponto (o ponto simples
    vai no SDO_POINT: ver TiposSdo.converter).

    Cada anel vira um trio no ELEM_INFO: (posição do primeiro valor em ORDINATES, tipo, 1).
    ORDINATES é a lista corrida x1, y1, x2, y2... de todos os anéis.
    """
    if geometria.geom_type == "MultiPoint":
        pontos = shapely.get_coordinates(geometria).ravel().tolist()
        return GTYPE_MULTIPONTO, [1, ETYPE_PONTO, len(pontos) // 2], pontos
    if geometria.geom_type in ("LineString", "MultiLineString"):
        linhas = list(geometria.geoms) if geometria.geom_type == "MultiLineString" else [geometria]
        elem_info, ordenadas = [], []
        for linha in linhas:
            elem_info += [len(ordenadas) + 1, ETYPE_LINHA, INTERPRETACAO_RETAS]
            ordenadas += shapely.get_coordinates(linha).ravel().tolist()
        return (GTYPE_LINHA if len(linhas) == 1 else GTYPE_MULTILINHA), elem_info, ordenadas
    if geometria.geom_type == "Polygon":
        poligonos, gtype = [geometria], GTYPE_POLIGONO
    elif geometria.geom_type == "MultiPolygon":
        poligonos, gtype = list(geometria.geoms), GTYPE_MULTIPOLIGONO
    else:
        raise ValueError(f"Tipo de geometria sem conversão: {geometria.geom_type}")

    elem_info: list[int] = []
    ordenadas: list[float] = []
    for poligono in poligonos:
        for indice, anel in enumerate([poligono.exterior, *poligono.interiors]):
            tipo = ETYPE_ANEL_EXTERNO if indice == 0 else ETYPE_BURACO
            elem_info += [len(ordenadas) + 1, tipo, INTERPRETACAO_RETAS]
            ordenadas += shapely.get_coordinates(anel).ravel().tolist()
    return gtype, elem_info, ordenadas


class TiposSdo:
    """Os tipos de objeto do Oracle Spatial, carregados uma vez por lote."""

    def __init__(self, con) -> None:
        self.geometria = con.gettype("MDSYS.SDO_GEOMETRY")
        self._elem_info = con.gettype("MDSYS.SDO_ELEM_INFO_ARRAY")
        self._ordenadas = con.gettype("MDSYS.SDO_ORDINATE_ARRAY")
        self._ponto = con.gettype("MDSYS.SDO_POINT_TYPE")

    def converter(self, geometria: BaseGeometry | None, srid: int = SRID_BANCO):
        """O objeto SDO_GEOMETRY (None para geometria nula ou vazia). Polígono vai no sentido que o
        Oracle exige (anel externo anti-horário)."""
        if geometria is None or geometria.is_empty:
            return None
        objeto = self.geometria.newobject()
        if geometria.geom_type == "Point":
            # No SDO_POINT, como o Oracle guarda: a API dos focos lê geometria.sdo_point.x e .y.
            ponto = self._ponto.newobject()
            ponto.X, ponto.Y = geometria.x, geometria.y
            objeto.SDO_GTYPE, objeto.SDO_SRID, objeto.SDO_POINT = GTYPE_PONTO, srid, ponto
            return objeto
        if geometria.geom_type in ("Polygon", "MultiPolygon"):
            geometria = shapely.orient_polygons(geometria, exterior_cw=False)
        gtype, elem_info, ordenadas = elementos(geometria)
        objeto.SDO_GTYPE = gtype
        objeto.SDO_SRID = srid
        objeto.SDO_ELEM_INFO = self._elem_info.newobject(elem_info)
        objeto.SDO_ORDINATES = self._ordenadas.newobject(ordenadas)
        return objeto
