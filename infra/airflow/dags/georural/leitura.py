"""Leitura de um arquivo da zona bruta em lotes de registros (atributos + geometria em 4326).

Formatos:
    zip da coleta por link   páginas parte-0001.geojson, parte-0002.geojson, ... (ver coleta.py)
    zip de shapefile         cada .shp, pelo shapefile.ler_em_lotes
    .geojson / .json         uma FeatureCollection

O GeoJSON é lido direto (json + shapely), sem o GDAL: os valores chegam como vieram (o GDAL
converteria as datas ISO e trocaria os nulos dos números por NaN), e é isso que vai para a staging
e para o payload da quarentena.

CRS do GeoJSON: sem "crs", ou com 4326 / CRS84 / 4674, as coordenadas ficam como estão (lon/lat;
entre SIRGAS 2000 e WGS 84 a diferença é de centímetros, a mesma decisão do shapefile.py). Outro
EPSG é reprojetado para 4326.
"""

import json
import math
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import shapely
import shapely.errors
import shapely.ops
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from georural import shapefile

COLUNA_GEOMETRIA = "geometry"  # o nome que a fonte_coluna dá à geometria (o mesmo da V14)
EXTENSOES_GEOJSON = (".geojson", ".json")
MANIFESTO_COLETA = "coleta.json"
_SEM_REPROJECAO = {4326, 4674}


class ArquivoInvalido(shapefile.ArquivoInvalido):
    """O arquivo não dá para ler. O arquivo inteiro é rejeitado."""


@dataclass
class Registro:
    linha: int          # numeração contínua no arquivo inteiro, a partir de 1
    camada: str         # de onde veio: o .shp ou a página do zip
    atributos: dict     # os valores como vieram, sem a geometria
    geometria: BaseGeometry | None


def lotes(caminho: Path, pasta: Path, tamanho_lote: int) -> Iterator[list[Registro]]:
    """Os registros do arquivo, em listas de até `tamanho_lote`."""
    lote: list[Registro] = []
    for registro in registros(caminho, pasta):
        lote.append(registro)
        if len(lote) >= tamanho_lote:
            yield lote
            lote = []
    if lote:
        yield lote


def registros(caminho: Path, pasta: Path) -> Iterator[Registro]:
    linha = 0
    for camada, atributos, geometria in _fontes(caminho, pasta):
        linha += 1
        yield Registro(linha, camada, atributos, geometria)


def _fontes(caminho: Path, pasta: Path):
    sufixo = caminho.suffix.lower()
    if sufixo in EXTENSOES_GEOJSON:
        yield from _ler_geojson(caminho, caminho.name)
        return
    if sufixo != ".zip":
        raise ArquivoInvalido(f"Formato não suportado no processamento: {caminho.name} "
                              "(aceitos: .zip com shapefile ou GeoJSON, .geojson, .json)")

    try:
        with zipfile.ZipFile(caminho) as arquivo_zip:
            tem_shp = any(nome.lower().endswith(".shp") for nome in arquivo_zip.namelist())
    except zipfile.BadZipFile as e:
        raise ArquivoInvalido(f"Não foi possível descompactar o arquivo: {e}") from e

    if tem_shp:
        # O shapefile.extrair confere os complementos (.shx, .dbf, .prj) de cada .shp.
        for shp in shapefile.extrair(caminho, pasta):
            for _, gdf in shapefile.ler_em_lotes(shp, 5000):
                coluna_geo = gdf.geometry.name
                colunas = [c for c in gdf.columns if c != coluna_geo]
                for atributos, geometria in zip(gdf[colunas].to_dict("records"), gdf.geometry):
                    yield shp.name, {k: _valor_python(v) for k, v in atributos.items()}, geometria
        return

    extraidos = _extrair(caminho, pasta)
    paginas = sorted(p for p in extraidos
                     if p.suffix.lower() in EXTENSOES_GEOJSON and p.name.lower() != MANIFESTO_COLETA)
    if not paginas:
        raise ArquivoInvalido("O zip não tem shapefile (.shp) nem GeoJSON.")
    for pagina in paginas:
        yield from _ler_geojson(pagina, pagina.name)
        pagina.unlink()  # libera o disco: a página já foi lida


def _extrair(caminho: Path, pasta: Path) -> list[Path]:
    raiz = pasta.resolve()
    raiz.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(caminho) as arquivo_zip:
            for entrada in arquivo_zip.infolist():
                destino = (raiz / entrada.filename).resolve()
                # Proteção contra "zip slip": um nome como ../../algo escreveria fora da pasta
                if raiz != destino and raiz not in destino.parents:
                    raise ArquivoInvalido(f"Entrada do zip aponta para fora da pasta: {entrada.filename}")
                arquivo_zip.extract(entrada, raiz)
    except zipfile.BadZipFile as e:
        raise ArquivoInvalido(f"Não foi possível descompactar o arquivo: {e}") from e
    return sorted(p for p in raiz.rglob("*") if p.is_file())


def _ler_geojson(caminho: Path, camada: str):
    try:
        with open(caminho, "rb") as arquivo:
            dados = json.load(arquivo)
    except ValueError as e:
        raise ArquivoInvalido(f"{camada} não é um JSON válido: {e}") from e
    if not isinstance(dados, dict) or not isinstance(dados.get("features"), list):
        raise ArquivoInvalido(f"{camada} não é uma FeatureCollection do GeoJSON.")

    reprojetar = _reprojecao(dados.get("crs"))
    for feicao in dados["features"]:
        atributos = dict(feicao.get("properties") or {})
        geometria = None
        if feicao.get("geometry"):
            try:
                geometria = shape(feicao["geometry"])
                if reprojetar is not None:
                    geometria = shapely.ops.transform(reprojetar, geometria)
            except (ValueError, TypeError, AttributeError, shapely.errors.GEOSException):
                geometria = None  # o pkg_quality reprova (GEOMETRIA_NULA) se ela for obrigatória
        yield camada, atributos, geometria


def _reprojecao(crs: dict | None):
    """Função de reprojeção para 4326, ou None quando as coordenadas já servem."""
    if not crs:
        return None
    nome = str((crs.get("properties") or {}).get("name", ""))
    if "CRS84" in nome.upper():
        return None
    achado = re.search(r"EPSG:+(\d+)", nome, re.IGNORECASE)
    if not achado or int(achado.group(1)) in _SEM_REPROJECAO:
        return None
    from pyproj import Transformer  # só quando precisa: o import é pesado
    return Transformer.from_crs(int(achado.group(1)), 4326, always_xy=True).transform


def _valor_python(valor):
    """Valor do pandas/numpy (shapefile) como tipo do Python, com NaN e NaT virando None."""
    if valor is None:
        return None
    if hasattr(valor, "item") and not isinstance(valor, (str, bytes)):
        try:
            valor = valor.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(valor, float) and math.isnan(valor):
        return None
    if hasattr(valor, "isoformat"):
        try:
            return None if str(valor) == "NaT" else valor.isoformat()
        except ValueError:
            return None
    return valor


# ------------------------------------------------------------------ staging

def texto(valor) -> str | None:
    """O valor como texto, do jeito que as regras do pkg_quality leem: número com ponto e sem
    notação científica, booleano SIM/NÃO, objeto e lista em JSON."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return "SIM" if valor else "NÃO"
    if isinstance(valor, int):
        return str(valor)
    if isinstance(valor, float):
        if math.isnan(valor) or math.isinf(valor):
            return None
        return format(Decimal(repr(valor)), "f")
    if isinstance(valor, (dict, list)):
        return json.dumps(valor, ensure_ascii=False)
    return str(valor)


def atributos_json(atributos: dict) -> str:
    return json.dumps({k: texto(v) for k, v in atributos.items()}, ensure_ascii=False)


def wkb(geometria: BaseGeometry | None) -> bytes | None:
    """WKB 2D, normalizado para o Oracle sem mudar a forma:

    * tira os vértices seguidos a menos de VERTICE_REPETIDO um do outro. Abaixo da tolerância
      do banco (5 cm) o Oracle reprova com ORA-13356 ("vértice duplicado"): na Reserva Legal do
      IAT eram 130 mil de 348 mil polígonos. O JTS nem considera isso erro;
    * põe o anel externo no sentido anti-horário que o Oracle exige (o shapefile e boa parte dos
      serviços guardam no horário).

    Erro de topologia de verdade (autocruzamento) continua como veio: quem decide é a política
    de geometria da regra (QUARENTENA ou CORRIGIR_E_REGISTRAR, no pkg_quality).
    """
    if geometria is None or geometria.is_empty:
        return None
    if geometria.geom_type in ("Polygon", "MultiPolygon", "LineString", "MultiLineString"):
        # Um anel minúsculo pode colapsar (o GEOS reclama de anel com 2 pontos) ou a geometria
        # sumir: aí vai como veio, e o Oracle julga.
        try:
            limpa = shapely.remove_repeated_points(geometria, VERTICE_REPETIDO)
        except shapely.errors.GEOSException:
            limpa = None
        if limpa is not None and not limpa.is_empty and limpa.geom_type == geometria.geom_type:
            geometria = limpa
    if geometria.geom_type in ("Polygon", "MultiPolygon"):
        geometria = shapely.orient_polygons(geometria, exterior_cw=False)
    return shapely.to_wkb(geometria, output_dimension=2)


# ~6 cm em graus (no PR, 1° de longitude dá ~100 km): um pouco acima da tolerância de 5 cm do
# banco, para o que o Oracle chamaria de vértice duplicado sair antes.
VERTICE_REPETIDO = 6e-7
