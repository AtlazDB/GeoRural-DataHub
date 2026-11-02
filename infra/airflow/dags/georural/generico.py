"""Processamento genérico: qualquer fonte, guiado pelo mapeamento (regra_campo), sem código por fonte.

É o caminho de tudo o que não é o shapefile do CAR: as fontes por link (municípios, focos de calor,
Reserva Legal e o próprio CAR pelo WFS) e os uploads de GeoJSON. Sem staging (US11.T04): as regras
rodam aqui (regras.py) e os aprovados vão direto para a Zona Tratada (tratada.py).

    1. baixa o arquivo da zona bruta
    2. primeira passada, em blocos: as colunas que vieram (detectar_colunas), as colunas do
       desempate e a chave natural de cada linha. Sem mapeamento, o arquivo fica
       AGUARDANDO_MAPEAMENTO
    3. desempate de código repetido (a regra do CAR: AT vence, depois o mais recente, a cancelada
       perde no empate, cópia idêntica sai). Só quando o mapeamento tem `situacao`. Os perdedores
       vão direto para a quarentena como DESCARTADO, com a justificativa; conflito de verdade é
       reprovado pela regra de chave repetida para uma pessoa decidir
    4. segunda passada, bloco a bloco: valida (regras.validar), grava os aprovados na Tratada só
       se mudaram (hash), os reprovados na quarentena e as correções de geometria. Commit a cada
       bloco: o undo do Autonomous tem 1 GB
    5. no fim, na carga-retrato, apaga o que não veio mais; conclui (substituir_pendentes)
    6. recalcula os indicadores que dependem do conjunto (IRL, IFC)

Se falhar no meio, os blocos já gravados ficam (estão certos), e a próxima carga só refaz o que
falta: o que já entrou tem o mesmo hash e é pulado.
"""

import logging
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import oracledb
import pandas as pd  # noqa: F401  (o desempate recebe um DataFrame)

from georural import bucket, colunas, gravacao, leitura, regras, sdo
from georural.banco import conectar
from georural.config import Config
from georural.tratada import Contagem, Tratada
from georural.validacao import ColunasDesempate, Desempate, planejar_desempate

log = logging.getLogger(__name__)

# Perdedor do desempate: vai direto para a quarentena, já descartado.
SQL_DESCARTE = """
    INSERT INTO registro_quarentena (execucao_id, numero_linha, campo, motivo, payload, entidade,
           chave_natural, tipo_erro, valor_recebido, dataset_versao_id, geometria,
           status_analise, justificativa, analisado_por, analisado_em)
    VALUES (:execucao_id, :numero_linha, :campo, :motivo, :payload, :entidade,
            :chave_natural, 'CHAVE_NATURAL_DUPLICADA', :chave_natural, :dataset_versao_id,
            :geometria, 'DESCARTADO', :justificativa, :autor, SYSTIMESTAMP)
"""

# Reprovado nas regras: um registro por linha, com o 1º erro e todos os motivos.
SQL_REPROVADO = """
    INSERT INTO registro_quarentena (execucao_id, numero_linha, campo, motivo, payload, entidade,
           chave_natural, tipo_erro, valor_recebido, dataset_versao_id, geometria)
    VALUES (:execucao_id, :numero_linha, :campo, :motivo, :payload, :entidade,
            :chave_natural, :tipo_erro, :valor_recebido, :dataset_versao_id, :geometria)
"""

SQL_CORRECAO = """
    INSERT INTO correcao_automatica (execucao_id, camada, numero_linha, chave_natural, coluna,
           erro_original, geometria_original, geometria_corrigida)
    VALUES (:execucao_id, :camada, :numero_linha, :chave_natural, :coluna, :erro_original,
            :original, :corrigida)
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
    # O que a carga fez na Tratada: só o que mudou é gravado.
    inseridos: int = 0
    alterados: int = 0
    iguais: int = 0
    removidos: int = 0
    correcoes: int = 0
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

        # O mapeamento vem antes da 1ª passada: ele diz as colunas do desempate e da chave.
        mapa = colunas.mapeamento(con, fonte_id)
        lista = regras.carregar_regras(con, fonte_id)
        mapeadas = sorted({r.coluna for r in lista if r.tipo != "GEOMETRIA"})
        cols = _colunas_desempate(mapa)
        valores_desempate: dict[str, list] = {c: [] for c in cols.todas()} if cols else {}

        # 1ª passada: as colunas que vieram, os valores do desempate e a chave natural de cada linha
        # (sem geometria: cabe na memória).
        levantamento = colunas.Levantamento()
        chaves: list = []
        with _etapa(resumo, "leitura"):
            for bloco in leitura.tabelas(local, pasta / "p1", cfg.tamanho_lote):
                levantamento.acrescentar_tabela(bloco)
                for nome, valores in valores_desempate.items():
                    valores.extend(bloco.atributos.get(nome, [None] * len(bloco)))
                if regras.regras_de_chave(lista):
                    chaves.extend(regras.chaves_naturais(bloco.textos(mapeadas), lista))
                resumo.lidos += len(bloco)
        resumo.colunas_novas = colunas.detectar_colunas(con, fonte_id, levantamento)
        con.commit()  # as colunas ficam registradas mesmo se o resto falhar: a tela de mapeamento usa
        log.info("%d registros, %d colunas (%d novas)", resumo.lidos, len(levantamento.nomes), resumo.colunas_novas)

        if not mapa.tabelas:
            mensagem = (f"Aguardando mapeamento: {len(levantamento.nomes)} colunas registradas. Mapeie as "
                        "colunas da fonte (botão #) e processe o arquivo de novo.")
            gravacao.aguardando_mapeamento(con, arquivo_id, execucao_id, mensagem)
            resumo.status = "AGUARDANDO_MAPEAMENTO"
            return resumo

        descartes = _descartes(_planejar(cols, valores_desempate), cols, valores_desempate) if cols else {}
        del valores_desempate
        # Chave repetida no arquivo inteiro (fora os perdedores do desempate): cada bloco reprova a sua parte.
        contagem_chaves = Counter(c for i, c in enumerate(chaves) if c is not None and i not in descartes)
        repetidas = {c for c, n in contagem_chaves.items() if n > 1}
        del chaves, contagem_chaves

        resumo.conjunto = gravacao.CONJUNTO_DA_TABELA.get(mapa.tabela, mapa.tabela)
        entidade = mapa.tabela if len(mapa.tabelas) == 1 else None
        geometria = next((r for r in lista if r.tipo == "GEOMETRIA"), None)

        try:
            gravou = False
            with _etapa(resumo, "contexto"):
                ctx = regras.carregar_contexto(con, lista)
            with con.cursor() as cur:
                versao = gravacao.criar_versao(cur, execucao_id, resumo.conjunto)
            con.commit()  # a versão é de quem grava os blocos, que dão commit um a um
            resumo.versao = versao.numero
            tratada = Tratada(con, lista, versao.id, resumo.conjunto)
            total = Contagem()
            gravou = False
            chaves_aprovadas: set[str] = set()

            posicao = 0  # posição, no arquivo inteiro, da 1ª linha do bloco
            for bloco in leitura.tabelas(local, pasta / "p2", cfg.tamanho_lote):
                with _etapa(resumo, "validacao"):
                    perdedores = [i for i in range(len(bloco)) if posicao + i in descartes]
                    ficam = [i for i in range(len(bloco)) if posicao + i not in descartes]
                    textos = bloco.textos(mapeadas).iloc[ficam].reset_index(drop=True)
                    geometrias = np.array([leitura.normalizar(bloco.geometrias[i]) for i in ficam], dtype=object)
                    v = regras.validar(textos, geometrias, lista, ctx, repetidas=repetidas)
                    reprovadas = set(v.reprovadas().tolist())
                    registros = textos.to_dict("records")
                    linhas = {d.tabela: [tratada.linha(d, registros[j], v.geometria[j])
                                         for j in range(len(ficam)) if j not in reprovadas]
                              for d in tratada.destinos}
                with _etapa(resumo, "gravacao"):
                    with con.cursor() as cur:
                        _gravar_descartes(cur, execucao_id, versao.id, entidade, cols,
                                          [(bloco, i, descartes[posicao + i]) for i in perdedores])
                        _gravar_reprovados(cur, execucao_id, versao.id, entidade, bloco, ficam, v)
                        _gravar_correcoes(cur, execucao_id, geometria, bloco, ficam, v)
                    total.somar(tratada.gravar(linhas))  # dá o commit do bloco
                    gravou = True
                chaves_aprovadas.update(c for j, c in enumerate(v.chave_natural) if c is not None and j not in reprovadas)
                resumo.descartados_automaticamente += len(perdedores)
                resumo.invalidos += len(reprovadas) + len(perdedores)
                resumo.validos += len(ficam) - len(reprovadas)
                resumo.correcoes += len(v.correcoes)
                posicao += len(bloco)
                log.info("Bloco gravado: %d de %d registros", posicao, resumo.lidos)

            with _etapa(resumo, "conclusao"):
                total.somar(tratada.finalizar())
                with con.cursor() as cur:
                    resumo.substituidos = gravacao.concluir(cur, arquivo_id, execucao_id, versao, resumo.lidos,
                                                            resumo.validos, resumo.invalidos, chaves_aprovadas)
                con.commit()
            resumo.inseridos, resumo.alterados = total.inseridos, total.alterados
            resumo.iguais, resumo.removidos = total.iguais, total.removidos
        except Exception:
            con.rollback()
            # Versão que não chegou a receber nenhum bloco não fica para trás vazia.
            if resumo.versao is not None and not gravou:
                _apagar_versao_vazia(con, execucao_id)
            raise

        # Nada mudou na Tratada: os indicadores continuam valendo.
        if resumo.inseridos or resumo.alterados or resumo.removidos:
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
        resumo.tempos[nome] = round(resumo.tempos.get(nome, 0) + time.monotonic() - inicio, 1)


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


def _descartes(plano: dict[str, Desempate], cols: ColunasDesempate, valores: dict[str, list]) -> dict:
    """Os perdedores do desempate: posição no arquivo -> Descarte. A n-ésima vez que um código
    aparece é a ocorrência n do plano."""
    if not plano:
        return {}
    ocorrencias: dict[str, int] = defaultdict(int)
    perdedores = {}
    for posicao, valor in enumerate(valores[cols.codigo]):
        codigo = leitura.texto(valor)
        codigo = codigo.strip() if codigo else codigo
        regra = plano.get(codigo)
        if regra is None:
            continue
        ocorrencias[codigo] += 1
        descarte = regra.descartes.get(ocorrencias[codigo])
        if descarte:
            perdedores[posicao] = descarte
    return perdedores


# ------------------------------------------------------------------ gravação

def _gravar_descartes(cur, execucao_id: int, versao_id: int, entidade: str | None,
                      cols: ColunasDesempate | None, descartes) -> None:
    if not descartes:
        return
    tipos = sdo.TiposSdo(cur.connection)
    cur.setinputsizes(payload=oracledb.DB_TYPE_CLOB, geometria=tipos.geometria)
    cur.executemany(SQL_DESCARTE, [
        {"execucao_id": execucao_id, "numero_linha": bloco.linhas[i], "campo": cols.codigo,
         "motivo": d.motivo[:400], "payload": leitura.atributos_json(bloco.registro(i)), "entidade": entidade,
         "chave_natural": (leitura.texto(bloco.atributos.get(cols.codigo, [None] * len(bloco))[i]) or "").strip()[:400]
                          or None,
         "dataset_versao_id": versao_id, "geometria": _sdo_ou_nada(tipos, leitura.normalizar(bloco.geometrias[i])),
         "justificativa": d.justificativa[:4000], "autor": gravacao.AUTOR_DESCARTE_AUTOMATICO}
        for bloco, i, d in descartes
    ])


def _gravar_reprovados(cur, execucao_id: int, versao_id: int, entidade: str | None, bloco: leitura.Tabela,
                       ficam: list[int], v: regras.Validacao) -> None:
    if v.erros.empty:
        return
    tipos = sdo.TiposSdo(cur.connection)
    cur.setinputsizes(payload=oracledb.DB_TYPE_CLOB, geometria=tipos.geometria)
    cur.executemany(SQL_REPROVADO, [
        {"execucao_id": execucao_id, "numero_linha": bloco.linhas[ficam[r.linha]], "campo": r.campo,
         "motivo": r.motivo or "Reprovado na validação.", "payload": leitura.atributos_json(bloco.registro(ficam[r.linha])),
         "entidade": entidade, "chave_natural": v.chave_natural.iloc[r.linha], "tipo_erro": r.tipo_erro,
         "valor_recebido": r.valor_recebido, "dataset_versao_id": versao_id,
         "geometria": _sdo_ou_nada(tipos, v.geometria[r.linha])}
        for r in regras.motivo_da_linha(v.erros).itertuples()
    ])


def _apagar_versao_vazia(con, execucao_id: int) -> None:
    try:
        with con.cursor() as cur:
            cur.execute("""DELETE FROM dataset_versao d WHERE d.execucao_id = :e
                            AND NOT EXISTS (SELECT 1 FROM registro_quarentena q WHERE q.dataset_versao_id = d.id)""",
                        e=execucao_id)
        con.commit()
    except oracledb.DatabaseError:  # a limpeza não pode esconder o erro original
        log.exception("Não deu para apagar a versão vazia da execução %s", execucao_id)
        con.rollback()


def _sdo_ou_nada(tipos: sdo.TiposSdo, geometria):
    """Na quarentena, geometria de tipo que o Oracle não guarda (coleção) fica de fora: o motivo
    já diz o tipo que veio."""
    try:
        return tipos.converter(geometria)
    except ValueError:
        return None


def _gravar_correcoes(cur, execucao_id: int, geometria: regras.Regra | None, bloco: leitura.Tabela,
                      ficam: list[int], v: regras.Validacao) -> None:
    if v.correcoes.empty:
        return
    tipos = sdo.TiposSdo(cur.connection)
    cur.setinputsizes(original=tipos.geometria, corrigida=tipos.geometria)
    cur.executemany(SQL_CORRECAO, [
        {"execucao_id": execucao_id, "camada": bloco.camadas[ficam[c.linha]][:260],
         "numero_linha": bloco.linhas[ficam[c.linha]], "chave_natural": v.chave_natural.iloc[c.linha],
         "coluna": geometria.coluna, "erro_original": c.erro_original,
         "original": tipos.converter(c.geometria_original), "corrigida": tipos.converter(c.geometria_corrigida)}
        for c in v.correcoes.itertuples()
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
