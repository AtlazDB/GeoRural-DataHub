"""Leitura de um arquivo da zona bruta em lotes de registros (atributos + geometria em 4326).

Formatos:
    zip da coleta por link   páginas parte-0001.geojson, parte-0002.geojson, ... (ver coleta.py)
    zip de shapefile         cada .shp, pelo shapefile.ler_em_lotes
    .geojson / .json         uma FeatureCollection
    .csv                     uma tabela; geometria opcional, por coluna WKT ou latitude/longitude

O GeoJSON é lido direto (json + shapely), sem o GDAL: os valores chegam como vieram (o GDAL
converteria as datas ISO e trocaria os nulos dos números por NaN), e é isso que vai para as regras
e para o payload da quarentena. O CSV também (csv do Python): todo valor chega como texto, do jeito
que está no arquivo, e quem converte é a regra (número com vírgula: formato VIRGULA).

CRS do GeoJSON: sem "crs", ou com 4326 / CRS84 / 4674, as coordenadas ficam como estão (lon/lat;
entre SIRGAS 2000 e WGS 84 a diferença é de centímetros, a mesma decisão do shapefile.py). Outro
EPSG é reprojetado para 4326. No CSV, WKT e latitude/longitude são lidos como 4326.
"""

import codecs
import csv
import json
import math
import re
import sys
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
EXTENSOES_CSV = (".csv",)
MANIFESTO_COLETA = "coleta.json"
_SEM_REPROJECAO = {4326, 4674}
_ACEITOS = "(aceitos: .zip com shapefile ou GeoJSON, .geojson, .json, .csv)"


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
    if sufixo in EXTENSOES_CSV:
        for atributos, geometria in TabelaCsv(caminho).linhas():
            yield caminho.name, atributos, geometria
        return
    if sufixo != ".zip":
        raise ArquivoInvalido(f"Formato não suportado no processamento: {caminho.name} {_ACEITOS}")

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
                geometria = None  # as regras reprovam (GEOMETRIA_NULA) se ela for obrigatória
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


# ------------------------------------------------------------------ texto e WKB

def texto(valor) -> str | None:
    """O valor como texto, do jeito que as regras (regras.py) leem: número com ponto e sem
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
    """WKB 2D da geometria normalizada (ver normalizar)."""
    normalizada = normalizar(geometria)
    return None if normalizada is None else shapely.to_wkb(normalizada, output_dimension=2)


def normalizar(geometria: BaseGeometry | None) -> BaseGeometry | None:
    """A geometria normalizada para o Oracle sem mudar a forma (None se vazia):

    * tira os vértices seguidos a menos de VERTICE_REPETIDO um do outro. Abaixo da tolerância
      do banco (5 cm) o Oracle reprova com ORA-13356 ("vértice duplicado"): na Reserva Legal do
      IAT eram 130 mil de 348 mil polígonos. O JTS nem considera isso erro;
    * põe o anel externo no sentido anti-horário que o Oracle exige (o shapefile e boa parte dos
      serviços guardam no horário).

    Erro de topologia de verdade (autocruzamento) continua como veio: quem decide é a política
    de geometria da regra (QUARENTENA ou CORRIGIR_E_REGISTRAR, em regras.py).
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
    return shapely.force_2d(geometria)


# ~6 cm em graus (no PR, 1° de longitude dá ~100 km): um pouco acima da tolerância de 5 cm do
# banco, para o que o Oracle chamaria de vértice duplicado sair antes.
VERTICE_REPETIDO = 6e-7


# ------------------------------------------------------------------ em tabela (US11.T04)

@dataclass
class Tabela:
    """Um bloco do arquivo, em colunas: o que o processamento sem staging valida e grava de uma vez."""
    linhas: list[int]            # numeração contínua no arquivo inteiro, a partir de 1
    camadas: list[str]           # de onde veio cada linha: o .shp ou a página do zip
    atributos: dict[str, list]   # coluna -> valores como tipos do Python (None no lugar de nulo)
    geometrias: list             # shapely ou None
    # Lido com geometria=False: as geometrias vêm todas None, e isto diz se o arquivo tem
    # geometria. None quando a geometria foi lida (aí quem diz são as próprias geometrias).
    geometria_declarada: bool | None = None

    def __len__(self) -> int:
        return len(self.linhas)

    def textos(self, colunas) -> "pd.DataFrame":
        """O texto de cada coluna pedida, do jeito que as regras leem (ver texto)."""
        import pandas as pd
        vazio = [None] * len(self)
        return pd.DataFrame({c: [texto(v) for v in self.atributos.get(c, vazio)] for c in colunas},
                            columns=list(colunas), index=range(len(self)), dtype="object")

    def registro(self, posicao: int) -> dict:
        return {nome: valores[posicao] for nome, valores in self.atributos.items()}


def tabelas(caminho: Path, pasta: Path, tamanho: int, geometria: bool = True) -> Iterator[Tabela]:
    """O arquivo em blocos de até `tamanho` linhas, na mesma ordem e com os mesmos valores que
    registros() entrega: o shapefile pelo pyogrio (GeoPandas), o GeoJSON pelo json.

    geometria=False (1ª passada do genérico): o shapefile é lido só com os atributos, o que
    poupa decodificar e reprojetar todas as formas; as geometrias do bloco vêm None e o
    `geometria_declarada` diz se o arquivo tem geometria. O GeoJSON é lido igual nos dois casos.
    """
    linha = 0
    for camada, atributos, geometrias, declarada in _blocos(caminho, pasta, geometria):
        for inicio in range(0, len(geometrias), tamanho):
            fim = min(inicio + tamanho, len(geometrias))
            yield Tabela(list(range(linha + inicio + 1, linha + fim + 1)), [camada] * (fim - inicio),
                         {k: v[inicio:fim] for k, v in atributos.items()}, geometrias[inicio:fim],
                         declarada)
        linha += len(geometrias)


def _blocos(caminho: Path, pasta: Path, geometria: bool = True):
    """(camada, atributos, geometrias, geometria_declarada) de cada pedaço do arquivo."""
    sufixo = caminho.suffix.lower()
    if sufixo in EXTENSOES_GEOJSON:
        yield *_geojson_em_colunas(caminho, caminho.name), None
        return
    if sufixo in EXTENSOES_CSV:
        yield from TabelaCsv(caminho).em_colunas(caminho.name, geometria)
        return
    if sufixo != ".zip":
        raise ArquivoInvalido(f"Formato não suportado no processamento: {caminho.name} {_ACEITOS}")
    try:
        with zipfile.ZipFile(caminho) as arquivo_zip:
            tem_shp = any(nome.lower().endswith(".shp") for nome in arquivo_zip.namelist())
    except zipfile.BadZipFile as e:
        raise ArquivoInvalido(f"Não foi possível descompactar o arquivo: {e}") from e

    if tem_shp:
        for shp in shapefile.extrair(caminho, pasta):
            declarada = None if geometria else shapefile.tem_geometria(shp)
            for _, tabela in shapefile.ler_em_lotes(shp, 5000, geometria=geometria):
                coluna_geo = tabela.geometry.name if geometria else None
                atributos = {c: [_valor_python(v) for v in tabela[c].tolist()]
                             for c in tabela.columns if c != coluna_geo}
                formas = list(tabela.geometry.array) if geometria else [None] * len(tabela)
                yield shp.name, atributos, formas, declarada
        return

    paginas = sorted(p for p in _extrair(caminho, pasta)
                     if p.suffix.lower() in EXTENSOES_GEOJSON and p.name.lower() != MANIFESTO_COLETA)
    if not paginas:
        raise ArquivoInvalido("O zip não tem shapefile (.shp) nem GeoJSON.")
    for pagina in paginas:
        yield *_geojson_em_colunas(pagina, pagina.name), None
        pagina.unlink()  # libera o disco: a página já foi lida


def _geojson_em_colunas(caminho: Path, camada: str):
    """Uma FeatureCollection inteira em colunas, lida pelo json como em _ler_geojson (não pelo GDAL:
    numa coluna com 0 e 25.5, o GDAL entregaria o 0 como 0.0, e um campo INTEIRO reprovaria)."""
    atributos: dict[str, list] = {}
    geometrias: list = []
    for posicao, (_, valores, geometria) in enumerate(_ler_geojson(caminho, camada)):
        for nome in valores:
            if nome not in atributos:  # na ordem em que a coluna apareceu, como em registros()
                atributos[nome] = [None] * posicao
        for nome, coluna in atributos.items():
            coluna.append(valores.get(nome))
        geometrias.append(geometria)
    return camada, atributos, geometrias


# ------------------------------------------------------------------ CSV (US13.T22)

# WKT de polígono passa fácil do limite padrão do módulo csv (128 KB por campo).
csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))

_SEPARADORES = ";,\t|"
_AMOSTRA_CSV = 64 * 1024
_BLOCO_CSV = 5000
# Coluna que, se existir, traz a geometria em WKT (sai dos atributos e vira a geometria).
_NOMES_WKT = ("wkt", "geometry", "geometria", "geom", "the_geom")
# Sem WKT, latitude + longitude dão um ponto (as duas continuam como atributos). x/y ficam de fora:
# em muita base são coordenadas em metros (UTM), não graus.
_NOMES_LATITUDE = ("latitude", "lat")
_NOMES_LONGITUDE = ("longitude", "lon", "long", "lng")


class TabelaCsv:
    """Um CSV: codificação, separador e de onde vem a geometria, descobertos pelo próprio arquivo.

    * codificação: UTF-8 (com ou sem BOM); se o arquivo não for UTF-8 válido, Latin-1 (o Excel em
      português grava assim);
    * separador: ; , tab ou |, pelo csv.Sniffer numa amostra do começo; na dúvida, o que mais
      aparece no cabeçalho;
    * valores: texto como está no arquivo; célula vazia vira None;
    * geometria: coluna WKT (wkt, geometry, geometria, geom, the_geom) ou, sem ela, latitude +
      longitude (latitude/lat e longitude/lon/long/lng), sempre em 4326. Valor que não dá geometria
      (WKT quebrado, coordenada fora da faixa) vira None: as regras reprovam se ela for obrigatória.
    """

    def __init__(self, caminho: Path):
        self.caminho = caminho
        self.codificacao = _codificacao(caminho)
        with open(caminho, newline="", encoding=self.codificacao) as arquivo:
            amostra = arquivo.read(_AMOSTRA_CSV)
        if not amostra.strip():
            raise ArquivoInvalido(f"{caminho.name} está vazio.")
        self.separador = _separador(amostra)
        cabecalho = next(csv.reader([amostra.splitlines()[0]], delimiter=self.separador))
        self.colunas = [c.strip() for c in cabecalho]
        if any(not c for c in self.colunas):
            raise ArquivoInvalido(f"{caminho.name}: o cabeçalho tem coluna sem nome.")
        repetidas = sorted({c for c in self.colunas if self.colunas.count(c) > 1})
        if repetidas:
            raise ArquivoInvalido(f"{caminho.name}: colunas repetidas no cabeçalho: {', '.join(repetidas)}")

        por_nome = {c.lower(): i for i, c in enumerate(self.colunas)}
        self._wkt = next((por_nome[n] for n in _NOMES_WKT if n in por_nome), None)
        latitude = next((por_nome[n] for n in _NOMES_LATITUDE if n in por_nome), None)
        longitude = next((por_nome[n] for n in _NOMES_LONGITUDE if n in por_nome), None)
        self._lat_lon = (latitude, longitude) if self._wkt is None and None not in (latitude, longitude) else None
        # Os atributos: todas as colunas, menos a do WKT (ela é a geometria).
        self._atributos = [i for i in range(len(self.colunas)) if i != self._wkt]

    @property
    def tem_geometria(self) -> bool:
        return self._wkt is not None or self._lat_lon is not None

    def linhas(self, geometria: bool = True) -> Iterator[tuple[dict, BaseGeometry | None]]:
        """(atributos, geometria) de cada linha. Linha mais curta que o cabeçalho completa com None;
        valor a mais (sem coluna) é ignorado. Linha toda vazia é pulada."""
        nomes = [self.colunas[i] for i in self._atributos]
        with open(self.caminho, newline="", encoding=self.codificacao) as arquivo:
            leitor = csv.reader(arquivo, delimiter=self.separador)
            next(leitor, None)  # o cabeçalho
            for celulas in leitor:
                if not any(c.strip() for c in celulas):
                    continue
                valores = [(c if c != "" else None) for c in celulas[:len(self.colunas)]]
                valores += [None] * (len(self.colunas) - len(valores))
                atributos = {nome: valores[i] for nome, i in zip(nomes, self._atributos)}
                yield atributos, (self._geometria(valores) if geometria else None)

    def em_colunas(self, camada: str, geometria: bool = True):
        """Os blocos de _blocos: (camada, atributos em colunas, geometrias, geometria_declarada)."""
        declarada = None if geometria else self.tem_geometria
        nomes = [self.colunas[i] for i in self._atributos]
        atributos: dict[str, list] = {n: [] for n in nomes}
        geometrias: list = []
        for valores, forma in self.linhas(geometria):
            for nome in nomes:
                atributos[nome].append(valores[nome])
            geometrias.append(forma)
            if len(geometrias) >= _BLOCO_CSV:
                yield camada, atributos, geometrias, declarada
                atributos, geometrias = {n: [] for n in nomes}, []
        if geometrias:
            yield camada, atributos, geometrias, declarada

    def _geometria(self, valores: list) -> BaseGeometry | None:
        if self._wkt is not None:
            return _de_wkt(valores[self._wkt])
        if self._lat_lon is not None:
            latitude, longitude = (_coordenada(valores[i]) for i in self._lat_lon)
            if latitude is None or longitude is None or abs(latitude) > 90 or abs(longitude) > 180:
                return None
            return shapely.Point(longitude, latitude)
        return None


def _codificacao(caminho: Path) -> str:
    """utf-8-sig se o arquivo inteiro for UTF-8 válido; senão latin-1 (que aceita qualquer byte)."""
    decodificador = codecs.getincrementaldecoder("utf-8")()
    try:
        with open(caminho, "rb") as arquivo:
            while bloco := arquivo.read(1024 * 1024):
                decodificador.decode(bloco)
            decodificador.decode(b"", final=True)
    except UnicodeDecodeError:
        return "latin-1"
    return "utf-8-sig"


def _separador(amostra: str) -> str:
    # O Sniffer olha linhas inteiras: corta a amostra na última quebra para não pegar linha pela metade.
    inteiras = amostra[:amostra.rfind("\n")] if "\n" in amostra else amostra
    try:
        return csv.Sniffer().sniff(inteiras, delimiters=_SEPARADORES).delimiter
    except csv.Error:
        cabecalho = amostra.splitlines()[0]
        contagem = {s: cabecalho.count(s) for s in _SEPARADORES}
        melhor = max(contagem, key=contagem.get)
        return melhor if contagem[melhor] else ","


def _de_wkt(texto: str | None) -> BaseGeometry | None:
    if not texto or not texto.strip():
        return None
    texto = texto.strip()
    if texto.upper().startswith("SRID="):  # EWKT do PostGIS: "SRID=4326;POINT(...)"
        texto = texto.split(";", 1)[-1]
    try:
        geometria = shapely.from_wkt(texto)
    except (shapely.errors.GEOSException, ValueError, TypeError):
        return None
    return None if geometria is None or geometria.is_empty else geometria


def _coordenada(texto: str | None) -> float | None:
    """Grau decimal com ponto ou vírgula ("-25.43" ou "-25,43")."""
    if not texto or not texto.strip():
        return None
    try:
        valor = float(texto.strip().replace(",", "."))
    except ValueError:
        return None
    return valor if math.isfinite(valor) else None
