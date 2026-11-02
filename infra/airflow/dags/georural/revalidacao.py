"""O "Revalidar" da quarentena (US11.T04): valida um registro com as mesmas regras da carga
(regras.py) e, se passar, grava na Zona Tratada (tratada.py). Era o pkg_quality.revalidar_registro
com o pkg_tratada.gravar_registro.

A DAG revalidar_registro chama daqui; o back-end dispara a DAG pela API REST do Airflow, espera e
devolve o resultado para a tela.

O registro validado é o payload corrigido e a geometria corrigida, se houver; senão, o que chegou
no arquivo. Ele entra na Tratada como se tivesse passado na carga que o trouxe: a versão é a dela,
e a rastreabilidade aponta para o mesmo arquivo da zona bruta.
"""

import json

import numpy as np
import oracledb
import pandas as pd
import shapely

from georural import leitura, regras
from georural.banco import conectar
from georural.config import Config
from georural.tratada import Tratada

APROVADO = "APROVADO"
REPROVADO = "REPROVADO"
RECUSADO = "RECUSADO"
_ENCERRADOS = ("DESCARTADO", "REVALIDADO", "SUBSTITUIDO")


class Recusa(Exception):
    """O registro não pode ser gravado (já encerrado, sem mapeamento, carga mais nova...). A
    mensagem vai para a tela."""


def _bytes_direto(cursor, metadado):
    if metadado.type_code is oracledb.DB_TYPE_BLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_RAW, arraysize=cursor.arraysize)
    if metadado.type_code is oracledb.DB_TYPE_CLOB:
        return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)


def revalidar(cfg: Config, quarentena_id: int) -> dict:
    """Devolve {status, erros, mensagem}: APROVADO (já gravou e encerrou o registro), REPROVADO
    (nada muda; a tela mostra os erros) ou RECUSADO (a mensagem diz por quê)."""
    with conectar(cfg) as con:
        try:
            resultado = _revalidar(con, quarentena_id)
            con.commit()
            return resultado
        except Recusa as e:
            con.rollback()
            return {"status": RECUSADO, "erros": [], "mensagem": str(e)}
        except Exception:
            con.rollback()
            raise


def _revalidar(con, quarentena_id: int) -> dict:
    with con.cursor() as cur:
        # O registro, travado: dois "Revalidar" ao mesmo tempo não gravam duas vezes.
        cur.execute("SELECT status_analise FROM registro_quarentena WHERE id = :id FOR UPDATE", id=quarentena_id)
        linha = cur.fetchone()
        if linha is None:
            raise Recusa(f"Registro de quarentena não encontrado: {quarentena_id}")
        if linha[0] in _ENCERRADOS:
            raise Recusa(f"O registro {quarentena_id} já está {linha[0]} e não muda mais.")

        cur.outputtypehandler = _bytes_direto
        cur.execute("""
            SELECT q.execucao_id, q.dataset_versao_id, q.entidade, NVL(q.payload_corrigido, q.payload),
                   CASE WHEN NVL(q.geometria_corrigida, q.geometria) IS NOT NULL
                        THEN SDO_UTIL.TO_WKBGEOMETRY(NVL(q.geometria_corrigida, q.geometria)) END,
                   a.fonte_id
              FROM registro_quarentena q
              JOIN execucao e ON e.id = q.execucao_id
              JOIN arquivo_bruto a ON a.id = e.arquivo_bruto_id
             WHERE q.id = :id""", id=quarentena_id)
        execucao_id, versao_id, entidade, payload, wkb, fonte_id = cur.fetchone()
        cur.outputtypehandler = None

        lista = regras.carregar_regras(con, fonte_id)
        if entidade:
            lista = [r for r in lista if r.tabela == entidade.lower()]
        tabelas = {r.tabela for r in lista}
        if not tabelas:
            raise Recusa(f"A fonte do registro não tem mapeamento de colunas para {(entidade or 'nenhuma tabela').lower()}: "
                         "cadastre o mapeamento antes de revalidar.")
        if len(tabelas) > 1:
            raise Recusa("O registro não diz a tabela de destino (entidade) e a fonte grava em mais de uma.")

        # Valida com as mesmas regras da carga. Chave repetida não se aplica: é um registro só.
        atributos = json.loads(payload) if payload else {}
        colunas = sorted({r.coluna for r in lista if r.tipo != "GEOMETRIA"})
        textos = {c: leitura.texto(atributos.get(c)) for c in colunas}
        geometria = leitura.normalizar(shapely.from_wkb(wkb)) if wkb else None
        ctx = regras.carregar_contexto(con, lista, linha=textos)
        v = regras.validar(pd.DataFrame([textos], columns=colunas, dtype="object"),
                           np.array([geometria], dtype=object), lista, ctx, repetidas=set())
        if not v.erros.empty:
            return {"status": REPROVADO, "mensagem": None, "erros": [
                {"tipo_erro": e.tipo_erro, "campo": e.campo, "motivo": e.motivo, "valor_recebido": e.valor_recebido}
                for e in v.erros.itertuples()]}

        # A versão: a da carga que trouxe o registro.
        if versao_id is None:
            cur.execute("SELECT MAX(id) FROM dataset_versao WHERE execucao_id = :e", e=execucao_id)
            versao_id = cur.fetchone()[0]
        versao, conjunto = None, None
        if versao_id is not None:
            cur.execute("SELECT versao, conjunto FROM dataset_versao WHERE id = :id", id=versao_id)
            versao, conjunto = cur.fetchone()

        tratada = Tratada(con, lista, versao_id, conjunto, carregar=False)
        destino = tratada.destinos[0]
        if "dataset_versao_id" in destino.colunas and versao_id is None:
            raise Recusa("A carga que trouxe o registro não criou versão de dados: não há versão para gravar.")
        nova = tratada.linha(destino, textos, v.geometria[0])
        _nao_voltar_para_tras(cur, destino, nova, versao, conjunto)

        try:
            inserida = tratada.gravar_um(cur, destino, nova)
        except oracledb.IntegrityError as e:
            if "ORA-01400" in str(e):  # a tabela exige um campo que o mapeamento não preenche
                raise Recusa(f"A tabela {destino.tabela} exige um campo que o mapeamento da fonte não preenche: "
                             f"{str(e)[:300]}") from e
            raise
        # Registro novo na Tratada conta na versão, como os que passaram na carga.
        if inserida and versao_id is not None:
            cur.execute("UPDATE dataset_versao SET registros = NVL(registros, 0) + 1 WHERE id = :id", id=versao_id)
        cur.execute("""
            UPDATE registro_quarentena
               SET status_analise = 'REVALIDADO', analisado_em = SYSTIMESTAMP,
                   dataset_versao_id = NVL(dataset_versao_id, :versao)
             WHERE id = :id""", versao=versao_id, id=quarentena_id)
    return {"status": APROVADO, "erros": [], "mensagem": None}


def _nao_voltar_para_tras(cur, destino, linha: dict, versao: int | None, conjunto: str | None) -> None:
    """Se uma carga mais nova já gravou este registro, revalidar o antigo apagaria o dado mais
    recente. "O mesmo registro" é o da chave; sem chave, o do imóvel; sem os dois, não há como comparar."""
    if versao is None or "dataset_versao_id" not in destino.colunas:
        return
    campos = [r.campo for r in destino.chaves] or (["cod_imovel"] if "cod_imovel" in linha["valores"] else [])
    if not campos:
        return
    condicoes = " AND ".join(f"d.{c} = :k{i}" for i, c in enumerate(campos))
    cur.execute(f"""SELECT MAX(dv.versao) FROM {destino.tabela} d
                      JOIN dataset_versao dv ON dv.id = d.dataset_versao_id
                     WHERE dv.conjunto = :conjunto AND {condicoes}""",
                conjunto=conjunto, **{f"k{i}": linha["valores"].get(c) for i, c in enumerate(campos)})
    mais_nova = cur.fetchone()[0]
    if mais_nova is not None and mais_nova > versao:
        raise Recusa(f"Uma carga mais recente (versão {int(mais_nova)}) já atualizou este registro. "
                     f"Revalidar o da versão {int(versao)} voltaria o dado para trás: descarte-o.")
