"""Processamento do shapefile de imóveis do CAR, de ponta a ponta.

Faz o mesmo que o ProcessamentoCarServiceImpl do back-end, só que fora da aplicação:
    1. baixa o .zip da zona bruta para um disco temporário
    2. lê cada .shp em lotes (a memória depende do lote, não do arquivo)
    3. valida cada imóvel
    4. grava aprovados (MERGE em imovel_rural) e reprovados (registro_quarentena)
    5. conclui a execução e o arquivo

Versão + imóveis + quarentena + conclusão ficam numa transação só: se der erro no meio,
nada fica pela metade (nem imóvel apontando para uma versão que falhou).
"""

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from georural import bucket, gravacao, shapefile
from georural.banco import conectar
from georural.config import Config
from georural.validacao import (COLUNA_AREA, COLUNA_ATUALIZACAO, COLUNA_COD_IMOVEL, COLUNA_CRIACAO,
                                COLUNA_SITUACAO, ValidadorImovel, planejar_desempate)

log = logging.getLogger(__name__)


@dataclass
class Resumo:
    arquivo_id: int
    execucao_id: int
    versao: int
    lidos: int = 0
    validos: int = 0
    invalidos: int = 0
    descartados_automaticamente: int = 0  # dentro de `invalidos`: repetições resolvidas pelo desempate
    substituidos: int = 0  # registros abertos de cargas anteriores que esta carga encerrou
    quarentena_iguais: int = 0  # dentro de `invalidos`: já estavam na quarentena, não foram gravados de novo


def preparar(cfg: Config, arquivo_id: int, ja_reservado: bool, dag_run_id: str | None) -> int:
    """Confere o arquivo, garante a reserva (PROCESSANDO) e abre a execução. Devolve o id dela.
    Vale para os dois caminhos (este e o generico.py).

    ja_reservado=True quando quem disparou (back-end ou coletar_fonte) já passou o arquivo para
    PROCESSANDO. Num disparo manual a reserva acontece aqui.
    """
    with conectar(cfg) as con:
        arquivo = gravacao.buscar_arquivo(con, arquivo_id)
        if arquivo is None:
            raise ValueError(f"Arquivo {arquivo_id} não existe")
        if not arquivo.processavel:
            raise ValueError(
                f"Arquivo {arquivo_id} ({arquivo.nome_original}, fonte {arquivo.sigla_fonte}) não é .zip, "
                ".geojson nem .json"
            )
        if not ja_reservado and not gravacao.reservar(con, arquivo_id, somente_recebido=False):
            raise RuntimeError(f"Arquivo {arquivo_id} já está sendo processado")
        return gravacao.iniciar_execucao(con, arquivo_id, dag_run_id)


def processar(cfg: Config, arquivo_id: int, execucao_id: int) -> Resumo:
    """Baixa, lê, valida e grava. Se algo falhar, desfaz tudo e propaga o erro
    (quem chama registra a falha — ver registrar_falha)."""
    with conectar(cfg) as con:
        arquivo = gravacao.buscar_arquivo(con, arquivo_id)

        with tempfile.TemporaryDirectory(prefix="car-") as tmp:
            pasta = Path(tmp)
            zip_local = bucket.baixar(cfg, arquivo.chave_objeto, pasta / "arquivo.zip")
            log.info("Baixado %s (%.1f MB)", arquivo.chave_objeto, zip_local.stat().st_size / 1e6)
            shapefiles = shapefile.extrair(zip_local, pasta / "extraido")
            zip_local.unlink()  # libera o disco: daqui para frente só os .shp importam

            validador = ValidadorImovel(_planejar_desempate(shapefiles))
            try:
                with con.cursor() as cur:
                    versao = gravacao.criar_versao(cur, execucao_id)
                    resumo = Resumo(arquivo_id=arquivo_id, execucao_id=execucao_id, versao=versao.numero)
                    fila = gravacao.carregar_fila(cur, gravacao.CONJUNTO_IMOVEIS, com_chave=True)

                    for shp in shapefiles:
                        log.info("Lendo %s", shp.name)
                        for primeira_linha, lote in shapefile.ler_em_lotes(shp, cfg.tamanho_lote):
                            _gravar_lote(cur, validador, lote, primeira_linha, versao, execucao_id, resumo, fila)
                            log.info("%s: %d lidos até agora", shp.name, resumo.lidos)

                    resumo.quarentena_iguais = fila.iguais
                    resumo.substituidos = gravacao.concluir(cur, arquivo_id, execucao_id, versao,
                                                            resumo.lidos, resumo.validos, resumo.invalidos)
                con.commit()
            except Exception:
                con.rollback()
                raise

    log.info("Arquivo %s processado: %s", arquivo_id, resumo)
    return resumo


def _planejar_desempate(shapefiles):
    """Passada rápida pelas colunas do desempate (sem geometria), para saber quem fica com cada
    código repetido antes de gravar o primeiro lote: a versão que vence pode vir depois."""
    colunas = [COLUNA_COD_IMOVEL, COLUNA_SITUACAO, COLUNA_AREA, COLUNA_ATUALIZACAO, COLUNA_CRIACAO]
    chaves = pd.concat([shapefile.ler_colunas(shp, colunas) for shp in shapefiles], ignore_index=True)
    plano = planejar_desempate(chaves)
    log.info("%d códigos repetidos no arquivo; %d repetições resolvidas sozinhas pelo desempate",
             len(plano), sum(len(regra.descartes) for regra in plano.values()))
    return plano


def _gravar_lote(cur, validador, lote, primeira_linha, versao, execucao_id, resumo: Resumo, fila) -> None:
    colunas = [c for c in lote.columns if c != lote.geometry.name]
    aprovados, reprovados = [], []

    for deslocamento, (atributos, geometria) in enumerate(
        zip(lote[colunas].to_dict("records"), lote.geometry)
    ):
        resultado = validador.validar(primeira_linha + deslocamento, atributos, geometria)
        (aprovados if resultado.aprovado else reprovados).append(resultado)

    gravacao.gravar_imoveis(cur, versao.id, aprovados)
    gravacao.gravar_quarentena(cur, execucao_id, versao.id, reprovados, fila)
    resumo.lidos += len(aprovados) + len(reprovados)
    resumo.validos += len(aprovados)
    resumo.invalidos += len(reprovados)
    resumo.descartados_automaticamente += sum(r.descartado_automaticamente for r in reprovados)


def registrar_falha(cfg: Config, arquivo_id: int, execucao_id: int | None, erro: BaseException | str) -> None:
    mensagem = str(erro) or type(erro).__name__
    with conectar(cfg) as con:
        gravacao.falhar(con, arquivo_id, execucao_id, mensagem)
