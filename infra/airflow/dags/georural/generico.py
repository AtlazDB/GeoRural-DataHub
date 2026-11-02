"""Processamento genérico: qualquer fonte, guiado pelo mapeamento (regra_campo), sem código por fonte.

É o caminho de tudo o que não é o shapefile do CAR: as fontes por link (municípios, focos de calor,
Reserva Legal e o próprio CAR pelo WFS) e os uploads de GeoJSON.

    1. baixa o arquivo da zona bruta
    2. primeira passada: as colunas que vieram (detectar_colunas) e, para o desempate, as colunas
       de código, situação, área e datas. Sem mapeamento, o arquivo fica AGUARDANDO_MAPEAMENTO
    3. desempate de código repetido (a regra do CAR: AT vence, depois o mais recente, a cancelada
       perde no empate, cópia idêntica sai). Só quando o mapeamento tem `situacao`. Os perdedores
       vão direto para a quarentena como DESCARTADO, com a justificativa; conflito de verdade segue
       para a staging, e o pkg_quality reprova as duas versões para uma pessoa decidir
    4. segunda passada: staging (atributos em JSON, geometria em WKB)
    5. pkg_quality.validate: as regras da fonte
    6. pkg_tratada.gravar_aprovados e gravar_reprovados: Tratada e quarentena
    7. conclui (substituir_pendentes) e apaga a staging
    8. recalcula os indicadores que dependem do conjunto (IRL, IFC)

Versão + staging + Tratada + quarentena + conclusão ficam numa transação só, como no car.py.
"""

import logging
import tempfile
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import oracledb
import pandas as pd

from georural import bucket, colunas, gravacao, leitura
from georural.banco import conectar
from georural.config import Config
from georural.validacao import ColunasDesempate, Desempate, planejar_desempate

log = logging.getLogger(__name__)

SQL_STAGING = """
    INSERT INTO staging_registro (execucao_id, camada, numero_linha, atributos, geometria_wkb)
    VALUES (:execucao_id, :camada, :numero_linha, :atributos, :geometria_wkb)
"""

# Perdedor do desempate: não passa pela staging, vai direto para a quarentena já descartado.
SQL_DESCARTE = """
    INSERT INTO registro_quarentena (execucao_id, numero_linha, campo, motivo, payload, entidade,
           chave_natural, tipo_erro, valor_recebido, dataset_versao_id, geometria,
           status_analise, justificativa, analisado_por, analisado_em)
    VALUES (:execucao_id, :numero_linha, :campo, :motivo, :payload, :entidade,
            :chave_natural, 'CHAVE_NATURAL_DUPLICADA', :chave_natural, :dataset_versao_id,
            pkg_quality.to_geometry(:geometria_wkb),
            'DESCARTADO', :justificativa, :autor, SYSTIMESTAMP)
"""

# Indicadores recalculados quando o conjunto muda (pkg_indicadores).
INDICADORES = {
    "imoveis_rurais": ("calcular_irl", "calcular_ifc"),
    "reserva_legal": ("calcular_irl",),
    "focos_calor": ("calcular_ifc",),
}


@dataclass
class Resumo:
    arquivo_id: int
    execucao_id: int
    status: str = "PROCESSADO"
    conjunto: str | None = None
    versao: int | None = None
    lidos: int = 0
    validos: int = 0
    invalidos: int = 0
    descartados_automaticamente: int = 0  # dentro de `invalidos`
    substituidos: int = 0
    colunas_novas: int = 0
    indicadores: str | None = None
    # Segundos de cada etapa, na ordem em que rodaram: a base para comparar mudanças no processamento.
    tempos: dict[str, float] = field(default_factory=dict)


def processar(cfg: Config, arquivo_id: int, execucao_id: int) -> Resumo:
    resumo = Resumo(arquivo_id=arquivo_id, execucao_id=execucao_id)
    with conectar(cfg) as con, tempfile.TemporaryDirectory(prefix="generico-") as tmp:
        pasta = Path(tmp)
        arquivo = gravacao.buscar_arquivo(con, arquivo_id)
        fonte_id = _fonte(con, arquivo_id)
        local = bucket.baixar(cfg, arquivo.chave_objeto, pasta / arquivo.nome_original)
        log.info("Baixado %s (%.1f MB)", arquivo.chave_objeto, local.stat().st_size / 1e6)

        # O mapeamento vem antes da 1ª passada: é ele que diz quais colunas o desempate usa.
        mapa = colunas.mapeamento(con, fonte_id)
        cols = _colunas_desempate(mapa)
        valores_desempate: dict[str, list] = {c: [] for c in cols.todas()} if cols else {}

        # 1ª passada: as colunas que vieram e os valores do desempate (sem geometria, cabe na memória).
        levantamento = colunas.Levantamento()
        with _etapa(resumo, "leitura"):
            for registro in leitura.registros(local, pasta / "p1"):
                levantamento.acrescentar(registro.atributos, registro.geometria)
                for nome, lista in valores_desempate.items():
                    lista.append(registro.atributos.get(nome))
                resumo.lidos += 1
        resumo.colunas_novas = colunas.detectar_colunas(con, fonte_id, levantamento)
        con.commit()  # as colunas ficam registradas mesmo se o resto falhar: a tela de mapeamento usa
        log.info("%d registros, %d colunas (%d novas)", resumo.lidos, len(levantamento.nomes), resumo.colunas_novas)

        if not mapa.tabelas:
            mensagem = (f"Aguardando mapeamento: {len(levantamento.nomes)} colunas registradas. Mapeie as "
                        "colunas da fonte (botão #) e processe o arquivo de novo.")
            gravacao.aguardando_mapeamento(con, arquivo_id, execucao_id, mensagem)
            resumo.status = "AGUARDANDO_MAPEAMENTO"
            return resumo

        plano = _planejar(cols, valores_desempate) if cols else {}
        del valores_desempate
        resumo.conjunto = gravacao.CONJUNTO_DA_TABELA.get(mapa.tabela, mapa.tabela)
        entidade = mapa.tabela if len(mapa.tabelas) == 1 else None

        try:
            with con.cursor() as cur:
                versao = gravacao.criar_versao(cur, execucao_id, resumo.conjunto)
                resumo.versao = versao.numero
                ocorrencias: dict[str, int] = defaultdict(int)

                with _etapa(resumo, "staging"):
                    for lote in leitura.lotes(local, pasta / "p2", cfg.tamanho_lote):
                        staging, descartes = [], []
                        for registro in lote:
                            descarte = _descarte(registro, plano, cols, ocorrencias)
                            (descartes if descarte else staging).append((registro, descarte))
                        _gravar_staging(cur, execucao_id, [r for r, _ in staging])
                        _gravar_descartes(cur, execucao_id, versao.id, entidade, cols, descartes)
                        resumo.descartados_automaticamente += len(descartes)

                log.info("Staging gravada; validando (pkg_quality.validate)")
                with _etapa(resumo, "validacao"):
                    cur.callproc("pkg_quality.validate", [execucao_id])
                with _etapa(resumo, "tratada"):
                    resumo.validos = int(cur.callfunc("pkg_tratada.gravar_aprovados", oracledb.DB_TYPE_NUMBER,
                                                      [execucao_id, versao.id]))
                with _etapa(resumo, "quarentena"):
                    reprovados = int(cur.callfunc("pkg_tratada.gravar_reprovados", oracledb.DB_TYPE_NUMBER,
                                                  [execucao_id, versao.id]))
                resumo.invalidos = reprovados + resumo.descartados_automaticamente
                with _etapa(resumo, "conclusao"):
                    resumo.substituidos = gravacao.concluir(cur, arquivo_id, execucao_id, versao,
                                                            resumo.lidos, resumo.validos, resumo.invalidos)
                    cur.execute("DELETE FROM staging_registro WHERE execucao_id = :id", id=execucao_id)
                    con.commit()
        except Exception:
            con.rollback()
            raise

        with _etapa(resumo, "indicadores"):
            resumo.indicadores = _recalcular_indicadores(con, resumo.conjunto)
    log.info("Arquivo %s processado: %s", arquivo_id, resumo)
    log.info("Tempos do arquivo %s (s): %s", arquivo_id, _tempos(resumo))
    return resumo


@contextmanager
def _etapa(resumo: Resumo, nome: str):
    """Mede uma etapa e guarda em resumo.tempos (vai para o XCom da DAG junto com o resumo)."""
    inicio = time.monotonic()
    try:
        yield
    finally:
        resumo.tempos[nome] = round(time.monotonic() - inicio, 1)


def _tempos(resumo: Resumo) -> str:
    return " · ".join(f"{nome} {segundos:.1f}" for nome, segundos in resumo.tempos.items()) \
        + f" · total {sum(resumo.tempos.values()):.1f}"


def _fonte(con, arquivo_id: int) -> int:
    with con.cursor() as cur:
        cur.execute("SELECT fonte_id FROM arquivo_bruto WHERE id = :id", id=arquivo_id)
        return int(cur.fetchone()[0])


# ------------------------------------------------------------------ desempate

def _colunas_desempate(mapa: colunas.Mapeamento) -> ColunasDesempate | None:
    """As colunas do desempate, quando o mapeamento tem situação e uma chave de uma coluna só (a
    regra é a do CAR). As datas vêm pelo nome, das colunas já registradas da fonte."""
    situacao = mapa.coluna_do_campo.get("situacao")
    if not situacao or len(mapa.chaves) != 1:
        return None

    def achar(prefixo):
        return next((n for n in mapa.colunas_da_fonte if n.lower().startswith(prefixo)), None)

    return ColunasDesempate(codigo=mapa.chaves[0], situacao=situacao,
                            area=mapa.coluna_do_campo.get("area_ha"),
                            atualizacao=achar("dat_atuali"), criacao=achar("dat_criac"))


def _planejar(cols: ColunasDesempate, valores: dict[str, list]) -> dict[str, Desempate]:
    plano = planejar_desempate(pd.DataFrame(valores), cols)
    log.info("Desempate (%s): %d códigos repetidos, %d repetições resolvidas sozinhas", cols, len(plano),
             sum(len(regra.descartes) for regra in plano.values()))
    return plano


def _descarte(registro: leitura.Registro, plano: dict[str, Desempate], cols: ColunasDesempate | None,
              ocorrencias: dict[str, int]):
    """O Descarte do registro, se o desempate o tirou; senão None (vai para a staging)."""
    if not plano or cols is None:
        return None
    codigo = leitura.texto(registro.atributos.get(cols.codigo))
    codigo = codigo.strip() if codigo else codigo
    regra = plano.get(codigo)
    if regra is None:
        return None
    ocorrencias[codigo] += 1
    return regra.descartes.get(ocorrencias[codigo])


# ------------------------------------------------------------------ gravação

def _gravar_staging(cur, execucao_id: int, registros: list[leitura.Registro]) -> None:
    if not registros:
        return
    cur.setinputsizes(atributos=oracledb.DB_TYPE_CLOB, geometria_wkb=oracledb.DB_TYPE_BLOB)
    cur.executemany(SQL_STAGING, [
        {"execucao_id": execucao_id, "camada": r.camada[:260], "numero_linha": r.linha,
         "atributos": leitura.atributos_json(r.atributos), "geometria_wkb": leitura.wkb(r.geometria)}
        for r in registros
    ])


def _gravar_descartes(cur, execucao_id: int, versao_id: int, entidade: str | None,
                      cols: ColunasDesempate | None, descartes) -> None:
    if not descartes:
        return
    cur.setinputsizes(payload=oracledb.DB_TYPE_CLOB, geometria_wkb=oracledb.DB_TYPE_BLOB)
    cur.executemany(SQL_DESCARTE, [
        {"execucao_id": execucao_id, "numero_linha": r.linha, "campo": cols.codigo,
         "motivo": d.motivo[:400], "payload": leitura.atributos_json(r.atributos), "entidade": entidade,
         "chave_natural": (leitura.texto(r.atributos.get(cols.codigo)) or "").strip()[:400] or None,
         "dataset_versao_id": versao_id,
         "geometria_wkb": leitura.wkb(r.geometria), "justificativa": d.justificativa[:4000],
         "autor": gravacao.AUTOR_DESCARTE_AUTOMATICO}
        for r, d in descartes
    ])


def _recalcular_indicadores(con, conjunto: str | None) -> str | None:
    """Recalcula os indicadores do conjunto, numa transação própria: se falhar, a carga continua
    valendo e o motivo fica no resumo da tarefa (dá para recalcular pela tela de indicadores)."""
    funcoes = INDICADORES.get(conjunto or "")
    if not funcoes:
        return None
    resultado = []
    try:
        with con.cursor() as cur:
            for funcao in funcoes:
                gravados = int(cur.callfunc(f"pkg_indicadores.{funcao}", oracledb.DB_TYPE_NUMBER, []))
                resultado.append(f"{funcao}: {gravados}")
        con.commit()
    except oracledb.DatabaseError as e:
        con.rollback()
        log.warning("Indicadores não recalculados: %s", e)
        return f"falhou: {e}"[:300]
    return ", ".join(resultado)
