"""Coleta das fontes por link: baixa tudo o que o serviço tem e grava um zip na zona bruta.

Um serviço ArcGIS ou WFS entrega no máximo alguns milhares de registros por pedido (o IAT,
2.000; o GeoServer do SICAR, 10.000). Por isso a coleta pede página por página:

    ArcGIS  resultOffset + resultRecordCount, ordenado pelo objectid
    WFS     startIndex + count (maxFeatures no 1.x), ordenado pela coluna-chave do mapeamento

A ordem fixa é o que garante que nenhuma página repete ou pula registro. Antes de começar, a
coleta pergunta o total (returnCountOnly / resultType=hits) e, no fim, confere: se o coletado não
bate com o total, ou se algum registro veio repetido, a coleta falha ("vieram X de Y") em vez de
gravar dado pela metade como se estivesse completo.

Cada página entra num zip já comprimida (parte-0001.geojson, parte-0002.geojson, ...), assim que
chega: o GeoJSON cru nunca fica inteiro em disco nem na memória. O zip é determinístico (data fixa
nas entradas, compressão fixa): o mesmo conteúdo gera o mesmo arquivo, e o hash diz se a fonte
mudou desde a última coleta.

Um link que não é serviço (zip, CSV, GeoJSON para baixar) é baixado de uma vez, conferindo o
tamanho anunciado pelo servidor.

Proteções da paginação: tamanho de página que cai pela metade quando o servidor dá erro 5xx (até
50), trava de páginas e remoção dos parâmetros de paginação que já vierem no link.
"""

import hashlib
import ipaddress
import json
import logging
import re
import socket
import ssl
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

TIPO_ARCGIS = "ARCGIS"
TIPO_WFS = "WFS"
TIPO_ARQUIVO = "ARQUIVO"

PAGINA_MINIMA = 50
# Teto da página do WFS: o SICAR aceita 10.000, mas uma página do CAR com 10.000 polígonos passa
# de 100 MB, e ela é lida inteira na memória para contar os registros.
PAGINA_WFS = 5000
PAGINA_ARCGIS = 2000
MAX_PAGINAS = 1000
TENTATIVAS = 3
PAUSA_ENTRE_PAGINAS = 0.5  # segundos: o IAT e o SICAR são serviços públicos, sem pressa
TEMPO_LIMITE = (15, 120)  # conexão, leitura (segundos): página que demora mais que isso é dividida
LIMITE_ARQUIVO = 2 * 1024**3  # o mesmo app.coleta.limite-bytes do back-end
MAX_REDIRECIONAMENTOS = 5
TAMANHO_BLOCO = 1024 * 1024

# Data fixa das entradas do zip: com a data de hoje, o mesmo conteúdo daria outro hash.
DATA_FIXA = (1980, 1, 1, 0, 0, 0)
NIVEL_COMPRESSAO = 6

# Parâmetros de paginação que a coleta controla: se vierem no link cadastrado, saem.
_PAGINACAO_ARCGIS = {"resultoffset", "resultrecordcount", "returncountonly", "returnidsonly", "orderbyfields", "f"}
_PAGINACAO_WFS = {"startindex", "count", "maxfeatures", "resulttype", "sortby"}


class ColetaFalhou(Exception):
    """A coleta não pôde terminar. A mensagem vai para fonte.ultima_coleta_mensagem."""


class _ErroDoServidor(Exception):
    """HTTP 500 ou tempo esgotado (é assim que o ArcGIS responde a uma página pesada demais), ou
    erro passageiro que não passou com as novas tentativas: vale tentar página menor."""


@dataclass
class Resultado:
    caminho: Path
    nome: str
    content_type: str
    tamanho: int
    hash_sha256: str
    tipo: str
    total: int | None = None
    coletados: int | None = None
    paginas: int = 0
    avisos: list[str] = field(default_factory=list)

    def mensagem(self) -> str:
        if self.coletados is None:
            return f"Arquivo baixado ({_formatar(self.tamanho)} bytes)."
        return f"{_formatar(self.coletados)} registros coletados em {self.paginas} página(s)."


def _formatar(numero: int) -> str:
    return f"{numero:,}".replace(",", ".")


# ------------------------------------------------------------------ link

def tipo(url: str) -> str:
    """A mesma regra do TesteLinkServiceImpl do back-end."""
    partes = urlsplit(url)
    caminho = partes.path.lower()
    if ("/featureserver/" in caminho or "/mapserver/" in caminho) and caminho.endswith("/query"):
        return TIPO_ARCGIS
    servico = next((v for k, v in parse_qsl(partes.query) if k.lower() == "service"), None)
    if servico and servico.lower() == "wfs":
        return TIPO_WFS
    return TIPO_ARQUIVO


def validar_url(url: str) -> None:
    """Recusa o que não é http(s) ou aponta para a rede interna, como o ValidadorUrlExterna do
    back-end: um link cadastrado não pode fazer o Airflow ler o banco, a VM ou os metadados da
    nuvem (169.254.169.254)."""
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise ColetaFalhou("O link precisa começar com http:// ou https://.")
    try:
        enderecos = {info[4][0] for info in socket.getaddrinfo(partes.hostname, partes.port or 443)}
    except socket.gaierror as e:
        raise ColetaFalhou(f"Não foi possível encontrar o servidor {partes.hostname}.") from e
    for endereco in enderecos:
        ip = ipaddress.ip_address(endereco.split("%")[0])
        if not ip.is_global:
            raise ColetaFalhou(f"O link aponta para um endereço interno ({partes.hostname}) e foi recusado.")


def _com_parametros(url: str, remover: set[str], acrescentar: dict) -> str:
    partes = urlsplit(url)
    mantidos = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True) if k.lower() not in remover]
    mantidos += [(k, str(v)) for k, v in acrescentar.items()]
    return urlunsplit(partes._replace(query=urlencode(mantidos, safe=",:*")))


def _parametro(url: str, nome: str) -> str | None:
    return next((v for k, v in parse_qsl(urlsplit(url).query) if k.lower() == nome.lower()), None)


# ------------------------------------------------------------------ HTTP

class _AdaptadorTls(HTTPAdapter):
    """Aceita as cifras da lista DEFAULT do OpenSSL, além das do Python.

    O GeoServer do SICAR só negocia AES256-GCM-SHA384 (troca de chave RSA), que o Python tira da
    lista padrão; o Java ainda aceita, por isso a coleta do back-end funcionava. O certificado e o
    nome do servidor continuam verificados, e o mínimo continua TLS 1.2.
    """

    def init_poolmanager(self, *args, **kwargs):
        contexto = ssl.create_default_context()
        contexto.minimum_version = ssl.TLSVersion.TLSv1_2
        contexto.set_ciphers("DEFAULT")
        kwargs["ssl_context"] = contexto
        return super().init_poolmanager(*args, **kwargs)


class ClienteHttp:
    """GET com novas tentativas e conferência de cada redirecionamento. Os testes trocam por um falso."""

    def __init__(self, cabecalhos: dict | None = None, validar: Callable[[str], None] = validar_url):
        self.sessao = requests.Session()
        self.sessao.mount("https://", _AdaptadorTls())
        self.sessao.headers.update({"User-Agent": "GeoRural-DataHub/1.0 (coleta)"})
        self.sessao.headers.update(cabecalhos or {})
        self.validar = validar

    def abrir(self, url: str, metodo: str = "GET", corpo: str | None = None, stream: bool = False):
        for _ in range(MAX_REDIRECIONAMENTOS + 1):
            self.validar(url)
            resposta = self.sessao.request(metodo, url, data=corpo, timeout=TEMPO_LIMITE,
                                           allow_redirects=False, stream=stream)
            if resposta.is_redirect:
                url = urljoin(url, resposta.headers["Location"])
                resposta.close()
                continue
            return resposta
        raise ColetaFalhou("O link redirecionou vezes demais.")

    def texto(self, url: str) -> bytes:
        """Corpo de um GET. HTTP 500 e tempo esgotado saem na hora (quem chama divide a
        página); erro passageiro (429, 502, 503, 504, conexão caída) ganha até TENTATIVAS novas
        tentativas."""
        espera = 2
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                resposta = self.abrir(url)
            except requests.Timeout as e:
                raise _ErroDoServidor(f"sem resposta em {TEMPO_LIMITE[1]} s") from e
            except requests.ConnectionError as e:
                motivo = f"sem resposta ({e.__class__.__name__})"
            else:
                if resposta.status_code < 400:
                    return resposta.content
                if resposta.status_code == 500:
                    raise _ErroDoServidor("HTTP 500")
                if resposta.status_code < 500 and resposta.status_code != 429:
                    raise ColetaFalhou(f"O servidor de origem respondeu HTTP {resposta.status_code}.")
                motivo = f"HTTP {resposta.status_code}"
            log.warning("Tentativa %d/%d falhou (%s): %s", tentativa, TENTATIVAS, motivo, url)
            if tentativa < TENTATIVAS:
                time.sleep(espera)
                espera *= 2
        raise _ErroDoServidor(motivo)

    def json(self, url: str) -> dict:
        try:
            return json.loads(self.texto(url))
        except _ErroDoServidor as e:
            raise ColetaFalhou(f"O servidor de origem falhou ({e}).") from e
        except ValueError as e:
            raise ColetaFalhou("O servidor de origem não respondeu JSON.") from e


# ------------------------------------------------------------------ serviços

class _Servico:
    """O que muda entre ArcGIS e WFS: como perguntar o total e como pedir uma página."""

    tipo: str
    pagina_inicial: int
    ordenacao: str | None

    def total(self) -> int: ...

    def pagina(self, inicio: int, tamanho: int) -> str: ...


class _ArcGIS(_Servico):
    tipo = TIPO_ARCGIS

    def __init__(self, url: str, cliente: ClienteHttp):
        self.url = url
        self.cliente = cliente
        # A descrição da camada (a URL sem o /query) diz o limite por pedido e o campo de id.
        camada = cliente.json(_com_parametros(url.split("?")[0].rsplit("/query", 1)[0], set(), {"f": "json"}))
        if "error" in camada:
            raise ColetaFalhou(f"O ArcGIS recusou o pedido: {camada['error'].get('message', camada['error'])}")
        paginacao = camada.get("advancedQueryCapabilities", {}).get("supportsPagination", True)
        if not paginacao:
            raise ColetaFalhou("Esta camada do ArcGIS não aceita paginação (supportsPagination=false).")
        self.ordenacao = camada.get("objectIdField") or "objectid"
        self.pagina_inicial = min(PAGINA_ARCGIS, int(camada.get("maxRecordCount") or PAGINA_ARCGIS))

    def total(self) -> int:
        resposta = self.cliente.json(_com_parametros(self.url, _PAGINACAO_ARCGIS,
                                                     {"returnCountOnly": "true", "f": "json"}))
        if "count" not in resposta:
            raise ColetaFalhou(f"O ArcGIS não informou o total de registros: {str(resposta)[:200]}")
        return int(resposta["count"])

    def pagina(self, inicio: int, tamanho: int) -> str:
        return _com_parametros(self.url, _PAGINACAO_ARCGIS, {
            "resultOffset": inicio, "resultRecordCount": tamanho,
            "orderByFields": self.ordenacao, "f": "geojson",
        })


class _WFS(_Servico):
    tipo = TIPO_WFS
    pagina_inicial = PAGINA_WFS

    def __init__(self, url: str, cliente: ClienteHttp, chaves: list[str]):
        self.url = url
        self.cliente = cliente
        versao = _parametro(url, "version") or "2.0.0"
        self.parametro_tamanho = "count" if versao.startswith("2") else "maxFeatures"
        # Sem coluna-chave mapeada (primeira coleta de uma fonte nova), fica a ordem natural do
        # GeoServer, que pagina pela chave primária da tabela. A conferência de repetidos no fim
        # pega o caso em que isso não vale.
        self.ordenacao = ",".join(chaves) or None

    def total(self) -> int:
        # resultType=hits responde em XML, só com o total: numberMatched (2.0) ou numberOfFeatures (1.x).
        url = _com_parametros(self.url, _PAGINACAO_WFS | {"outputformat"}, {"resultType": "hits"})
        try:
            texto = self.cliente.texto(url).decode("utf-8", "replace")
        except _ErroDoServidor as e:
            raise ColetaFalhou(f"O WFS falhou ao informar o total ({e}).") from e
        achado = re.search(r'number(?:Matched|OfFeatures)="(\d+)"', texto)
        if not achado:
            raise ColetaFalhou(f"O WFS não informou o total de registros: {texto[:200]}")
        return int(achado.group(1))

    def pagina(self, inicio: int, tamanho: int) -> str:
        parametros = {"startIndex": inicio, self.parametro_tamanho: tamanho}
        if self.ordenacao:
            parametros["sortBy"] = self.ordenacao
        return _com_parametros(self.url, _PAGINACAO_WFS, parametros)


# ------------------------------------------------------------------ coleta

def coletar(url: str, pasta: Path, sigla: str, *, chaves: list[str] | None = None,
            metodo: str = "GET", cabecalhos: dict | None = None, corpo: str | None = None,
            cliente: ClienteHttp | None = None, pausa: float = PAUSA_ENTRE_PAGINAS,
            progresso: Callable[[str], None] = lambda _: None) -> Resultado:
    """Coleta o link inteiro em `pasta`. Serviço vira um zip de páginas; arquivo é baixado como veio."""
    cliente = cliente or ClienteHttp(cabecalhos)
    tipo_link = tipo(url)
    if tipo_link == TIPO_ARQUIVO:
        return _baixar_arquivo(url, pasta, sigla, cliente, metodo, corpo)
    servico = _ArcGIS(url, cliente) if tipo_link == TIPO_ARCGIS else _WFS(url, cliente, chaves or [])
    return _paginar(servico, url, pasta, sigla, pausa, progresso)


def _paginar(servico: _Servico, url: str, pasta: Path, sigla: str, pausa: float,
             progresso: Callable[[str], None]) -> Resultado:
    total = servico.total()
    if total == 0:
        raise ColetaFalhou("O serviço respondeu que não há nenhum registro para este link.")
    progresso(f"Coletando: 0 de {_formatar(total)} registros.")

    nome = f"{sigla.lower()}.zip"
    caminho = pasta / nome
    tamanho = servico.pagina_inicial
    coletados = paginas = repetidos = 0
    vistos: set = set()

    with zipfile.ZipFile(caminho, "w") as zip_:
        while coletados < total:
            if paginas >= MAX_PAGINAS:
                raise ColetaFalhou(f"A coleta passou de {MAX_PAGINAS} páginas e foi interrompida "
                                   f"({_formatar(coletados)} de {_formatar(total)} registros).")
            try:
                conteudo = servico.cliente.texto(servico.pagina(coletados, tamanho))
            except _ErroDoServidor as e:
                if tamanho <= PAGINA_MINIMA:
                    raise ColetaFalhou(f"O servidor de origem falhou mesmo com páginas de {PAGINA_MINIMA} "
                                       f"registros ({e}). Vieram {_formatar(coletados)} de {_formatar(total)}.") from e
                tamanho = max(PAGINA_MINIMA, tamanho // 2)
                log.warning("Página com erro (%s): tentando com %d registros por pedido", e, tamanho)
                continue

            feicoes = _feicoes(conteudo)
            if not feicoes:
                break
            for feicao in feicoes:
                identificador = feicao.get("id")
                if identificador is not None:
                    if identificador in vistos:
                        repetidos += 1
                    vistos.add(identificador)

            paginas += 1
            _gravar_entrada(zip_, f"parte-{paginas:04d}.geojson", conteudo)
            coletados += len(feicoes)
            # O servidor pode entregar menos do que o pedido (o próprio limite dele): a próxima
            # página já pede esse tamanho.
            tamanho = min(tamanho, len(feicoes)) if coletados < total else tamanho
            progresso(f"Coletando: {_formatar(coletados)} de {_formatar(total)} registros "
                      f"({paginas} página(s)).")
            time.sleep(pausa)

        if repetidos:
            raise ColetaFalhou(f"{_formatar(repetidos)} registros vieram repetidos entre as páginas: a ordem do "
                               "serviço não é estável. Cadastre a coluna-chave no mapeamento para ordenar.")
        if coletados != total:
            # A fonte pode ter mudado durante a coleta (focos de calor são atualizados o dia todo):
            # se o total de agora bate com o coletado, o retrato está completo.
            total_agora = servico.total()
            if coletados != total_agora:
                raise ColetaFalhou(f"Vieram {_formatar(coletados)} de {_formatar(total)} registros.")
            total = total_agora

        _gravar_entrada(zip_, "coleta.json", json.dumps({
            "url": url, "tipo": servico.tipo, "ordenacao": servico.ordenacao,
            "total": total, "paginas": paginas,
        }, ensure_ascii=False, indent=2).encode("utf-8"))

    return Resultado(caminho=caminho, nome=nome, content_type="application/zip",
                     tamanho=caminho.stat().st_size, hash_sha256=_hash(caminho), tipo=servico.tipo,
                     total=total, coletados=coletados, paginas=paginas)


def _feicoes(conteudo: bytes) -> list[dict]:
    try:
        dados = json.loads(conteudo)
    except ValueError as e:
        raise ColetaFalhou(f"Uma página não veio em GeoJSON: {conteudo[:200]!r}") from e
    if isinstance(dados, dict) and "error" in dados:
        raise ColetaFalhou(f"O serviço recusou uma página: {dados['error']}")
    if not isinstance(dados, dict) or not isinstance(dados.get("features"), list):
        raise ColetaFalhou("Uma página não veio como FeatureCollection do GeoJSON.")
    return dados["features"]


def _gravar_entrada(zip_: zipfile.ZipFile, nome: str, conteudo: bytes) -> None:
    info = zipfile.ZipInfo(nome, date_time=DATA_FIXA)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zip_.writestr(info, conteudo, compresslevel=NIVEL_COMPRESSAO)


def _baixar_arquivo(url: str, pasta: Path, sigla: str, cliente: ClienteHttp, metodo: str,
                    corpo: str | None) -> Resultado:
    resposta = cliente.abrir(url, metodo=metodo or "GET", corpo=corpo, stream=True)
    if resposta.status_code >= 400:
        raise ColetaFalhou(f"O servidor de origem respondeu HTTP {resposta.status_code}.")
    nome = _nome_do_arquivo(url, resposta.headers.get("Content-Disposition"), sigla)
    caminho = pasta / nome
    anunciado = resposta.headers.get("Content-Length")
    recebido = 0
    with open(caminho, "wb") as arquivo:
        for bloco in resposta.iter_content(TAMANHO_BLOCO):
            recebido += len(bloco)
            if recebido > LIMITE_ARQUIVO:
                raise ColetaFalhou(f"O arquivo passou do limite de {LIMITE_ARQUIVO // 1024**3} GB.")
            arquivo.write(bloco)
    if anunciado and int(anunciado) != recebido and not resposta.headers.get("Content-Encoding"):
        raise ColetaFalhou(f"O download veio incompleto: {_formatar(recebido)} de {_formatar(int(anunciado))} bytes.")
    content_type = (resposta.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
    return Resultado(caminho=caminho, nome=nome, content_type=content_type, tamanho=recebido,
                     hash_sha256=_hash(caminho), tipo=TIPO_ARQUIVO)


def _nome_do_arquivo(url: str, disposicao: str | None, sigla: str) -> str:
    achado = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposicao or "")
    nome = achado.group(1) if achado else urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    nome = re.sub(r"[^A-Za-z0-9._-]", "_", nome)[:120]
    return nome if "." in nome else f"{sigla.lower()}.bin"


def _hash(caminho: Path) -> str:
    sha = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(TAMANHO_BLOCO), b""):
            sha.update(bloco)
    return sha.hexdigest()
