"""Extração do .zip e leitura do shapefile em lotes (mesmas regras do LeitorShapefile do back-end)."""

import zipfile
from collections.abc import Iterator
from pathlib import Path

import pyogrio

COMPLEMENTOS_OBRIGATORIOS = ("shx", "dbf", "prj")
CHARSET_PADRAO = "ISO-8859-1"
SRID_DESTINO = 4326


class ArquivoInvalido(Exception):
    """O arquivo não dá para processar (zip quebrado, falta .dbf...). O arquivo inteiro é rejeitado."""


def extrair(zip_path: Path, pasta: Path) -> list[Path]:
    """Descompacta o .zip e devolve os .shp encontrados, em ordem de nome.

    O CAR às vezes divide o estado em mais de um .shp. Cada um precisa ter .shx, .dbf e .prj
    com o mesmo nome; se faltar algum, o arquivo inteiro é rejeitado.
    """
    raiz = pasta.resolve()
    try:
        with zipfile.ZipFile(zip_path) as arquivo_zip:
            for entrada in arquivo_zip.infolist():
                destino = (raiz / entrada.filename).resolve()
                # Proteção contra "zip slip": um nome como ../../algo escreveria fora da pasta
                if raiz != destino and raiz not in destino.parents:
                    raise ArquivoInvalido(f"Entrada do zip aponta para fora da pasta: {entrada.filename}")
                arquivo_zip.extract(entrada, raiz)
    except zipfile.BadZipFile as e:
        raise ArquivoInvalido(f"Não foi possível descompactar o arquivo: {e}") from e

    shapefiles = sorted(p for p in raiz.rglob("*") if p.is_file() and p.suffix.lower() == ".shp")
    if not shapefiles:
        raise ArquivoInvalido("Nenhum arquivo .shp encontrado dentro do zip")

    faltando = [
        f"{shp.stem}.{ext}"
        for shp in shapefiles
        for ext in COMPLEMENTOS_OBRIGATORIOS
        if _complemento(shp, ext) is None
    ]
    if faltando:
        raise ArquivoInvalido("Arquivos obrigatórios ausentes: " + ", ".join(faltando))
    return shapefiles


def _complemento(shp: Path, ext: str) -> Path | None:
    """Arquivo irmão do .shp com a extensão pedida, ignorando maiúsculas (CAR.SHP + car.dbf)."""
    for irmao in shp.parent.iterdir():
        if irmao.stem.lower() == shp.stem.lower() and irmao.suffix.lower() == "." + ext:
            return irmao
    return None


def charset(shp: Path) -> str:
    """Charset do .dbf, lido do .cpg. Sem .cpg (ou com conteúdo estranho), ISO-8859-1."""
    cpg = _complemento(shp, "cpg")
    if cpg is None:
        return CHARSET_PADRAO
    nome = cpg.read_text(encoding="ascii", errors="ignore").strip()
    if not nome:
        return CHARSET_PADRAO
    # O ArcGIS às vezes grava só o número da code page, ex.: "1252"
    return f"cp{nome}" if nome.isdigit() else nome


def ler_colunas(shp: Path, nomes: list[str]):
    """Só as colunas pedidas, sem a geometria: uma passada rápida pelo arquivo inteiro.

    Os nomes são procurados ignorando maiúsculas e voltam com o nome pedido; coluna que o
    arquivo não tem fica de fora do resultado.
    """
    por_nome = {campo.lower(): campo for campo in pyogrio.read_info(shp)["fields"]}
    existentes = {por_nome[n.lower()]: n for n in nomes if n.lower() in por_nome}
    tabela = pyogrio.read_dataframe(
        shp, columns=list(existentes), read_geometry=False, encoding=charset(shp)
    )
    return tabela.rename(columns=existentes)


def contar(shp: Path) -> int:
    """Quantos registros o shapefile tem, sem ler os dados (para a barra de progresso)."""
    return int(pyogrio.read_info(shp)["features"])


def ler_em_lotes(shp: Path, tamanho_lote: int) -> Iterator[tuple[int, "object"]]:
    """Lê o shapefile em pedaços de `tamanho_lote` registros, já em SRID 4326.

    Devolve (número da primeira linha do lote, GeoDataFrame). A memória usada depende do
    tamanho do lote, não do tamanho do arquivo.
    """
    info = pyogrio.read_info(shp)
    total = int(info["features"])
    codificacao = charset(shp)

    for inicio in range(0, total, tamanho_lote):
        lote = pyogrio.read_dataframe(
            shp, skip_features=inicio, max_features=tamanho_lote, encoding=codificacao
        )
        # O CAR chega em SIRGAS 2000 (4674); o banco guarda tudo em 4326. Entre os dois a diferença
        # é de centímetros, mas reprojetar deixa o pipeline certo para qualquer outra fonte.
        if lote.crs is not None and lote.crs.to_epsg() != SRID_DESTINO:
            lote = lote.to_crs(SRID_DESTINO)
        yield inicio + 1, lote
