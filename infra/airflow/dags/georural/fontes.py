"""O lado do banco da coleta por link: pedidos na fila, andamento e o arquivo gravado na zona bruta.

O "Executar" da tela (back-end) só marca fonte.coleta_solicitada_em. O
coleta_fontes reserva esses pedidos e dispara o coletar_fonte, que vai escrevendo o andamento em
fonte.ultima_coleta_mensagem e, no fim, grava o arquivo_bruto RECEBIDO (origem COLETA), que o
varredor_bruta pega como qualquer upload.

A tela mostra "coletando" enquanto houver pedido na fila ou uma mensagem sem ultima_coleta_em.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import oracledb

PREFIXO_ANDAMENTO = "Coletando"


@dataclass(frozen=True)
class FontePorLink:
    id: int
    sigla: str
    url: str
    metodo: str
    cabecalhos: dict
    corpo: str | None


def listar_pedidos(con, limite: int) -> list[int]:
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM fonte
             WHERE coleta_solicitada_em IS NOT NULL AND url_origem IS NOT NULL
             ORDER BY coleta_solicitada_em
             FETCH FIRST :limite ROWS ONLY
            """,
            limite=limite,
        )
        return [linha[0] for linha in cur.fetchall()]


def reservar(con, fonte_id: int) -> bool:
    """Tira o pedido da fila e marca "coletando". Devolve False se outro varredor pegou antes."""
    with con.cursor() as cur:
        cur.execute(
            """
            UPDATE fonte
               SET coleta_solicitada_em = NULL, ultima_coleta_em = NULL,
                   ultima_coleta_mensagem = :mensagem
             WHERE id = :id AND coleta_solicitada_em IS NOT NULL
            """,
            id=fonte_id,
            mensagem=f"{PREFIXO_ANDAMENTO}: na fila do Airflow.",
        )
        reservado = cur.rowcount == 1
    con.commit()
    return reservado


def buscar(con, fonte_id: int) -> FontePorLink:
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT id, sigla, url_origem, NVL(metodo_http, 'GET'), cabecalhos, corpo_requisicao
              FROM fonte WHERE id = :id
            """,
            id=fonte_id,
        )
        linha = cur.fetchone()
    if not linha:
        raise ValueError(f"Fonte {fonte_id} não existe")
    if not linha[2]:
        raise ValueError(f"A fonte {linha[1]} não tem link de origem")
    cabecalhos = _ler(linha[4])
    return FontePorLink(id=linha[0], sigla=linha[1], url=linha[2], metodo=linha[3],
                        cabecalhos=json.loads(cabecalhos) if cabecalhos else {}, corpo=_ler(linha[5]))


def _ler(valor):
    return valor.read() if isinstance(valor, oracledb.LOB) else valor


def colunas_chave(con, fonte_id: int) -> list[str]:
    """Colunas marcadas como chave no mapeamento da fonte: a ordem das páginas do WFS."""
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT fc.nome_coluna
              FROM regra_campo r JOIN fonte_coluna fc ON fc.id = r.fonte_coluna_id
             WHERE r.fonte_id = :id AND r.chave = 1
             ORDER BY fc.ordem NULLS LAST, fc.nome_coluna
            """,
            id=fonte_id,
        )
        return [linha[0] for linha in cur.fetchall()]


def registrar_andamento(con, fonte_id: int, mensagem: str) -> None:
    with con.cursor() as cur:
        cur.execute("UPDATE fonte SET ultima_coleta_mensagem = :mensagem WHERE id = :id",
                    id=fonte_id, mensagem=mensagem[:1000])
    con.commit()


def registrar_fim(con, fonte_id: int, mensagem: str) -> None:
    with con.cursor() as cur:
        cur.execute(
            """
            UPDATE fonte SET ultima_coleta_em = SYSTIMESTAMP, ultima_coleta_mensagem = :mensagem
             WHERE id = :id
            """,
            id=fonte_id,
            mensagem=mensagem[:1000],
        )
    con.commit()


def ultimo_hash(con, fonte_id: int) -> tuple[int, str] | None:
    """O último arquivo da fonte (id, hash): se a coleta der o mesmo hash, nada muda."""
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT id, hash_sha256 FROM arquivo_bruto
             WHERE fonte_id = :id
             ORDER BY recebido_em DESC, id DESC
             FETCH FIRST 1 ROW ONLY
            """,
            id=fonte_id,
        )
        linha = cur.fetchone()
    return (linha[0], linha[1]) if linha else None


def chave_na_zona_bruta(zona_bruta: str, sigla: str, nome: str) -> str:
    """O mesmo formato do ArquivoBrutoServiceImpl.chaveNaZonaBruta do back-end."""
    hoje = datetime.now(timezone.utc).date().isoformat()
    return f"{zona_bruta}/{sigla}/{hoje}/{uuid.uuid4().hex[:8]}_{nome}"


def gravar_arquivo(con, fonte_id: int, chave: str, nome: str, tamanho: int, content_type: str,
                   hash_sha256: str) -> int:
    with con.cursor() as cur:
        id_var = cur.var(oracledb.DB_TYPE_NUMBER)
        cur.execute(
            """
            INSERT INTO arquivo_bruto (fonte_id, chave_objeto, nome_original, tamanho_bytes,
                                       content_type, hash_sha256, status, origem)
            VALUES (:fonte_id, :chave, :nome, :tamanho, :content_type, :hash, 'RECEBIDO', 'COLETA')
            RETURNING id INTO :id_saida
            """,
            fonte_id=fonte_id, chave=chave, nome=nome, tamanho=tamanho,
            content_type=content_type, hash=hash_sha256, id_saida=id_var,
        )
        arquivo_id = int(id_var.getvalue()[0])
    con.commit()
    return arquivo_id
