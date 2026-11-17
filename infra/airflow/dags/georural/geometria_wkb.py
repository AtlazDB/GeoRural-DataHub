"""Preenche a geometria_wkb (V30 do back-end) das linhas que já existiam antes dela.

Depois da V30, a carga grava o WKB junto com a geometria (gravacao.py e tratada.py). Mas a carga
pula o que não mudou (mesmo hash), então as linhas antigas só ganham o WKB por aqui. Roda uma vez
em cada ambiente (DAG preencher_geometria_wkb) e pode rodar de novo sem problema: só mexe em linha
com geometria e sem WKB.

A conversão é do próprio Oracle (SDO_UTIL.TO_WKBGEOMETRY), na hora do UPDATE: nenhuma geometria
sai do banco. Os ROWIDs pendentes são lidos uma vez, e o UPDATE vai em lotes com commit em cada um
(o undo do Autonomous é limitado). Se cair no meio, rodar de novo continua de onde parou.
"""

import logging

from georural.banco import conectar
from georural.config import Config

log = logging.getLogger(__name__)

# As tabelas com a coluna geometria_wkb (V30).
TABELAS = ("imovel_rural", "input_feature")

_PENDENTES = "SELECT ROWIDTOCHAR(ROWID) FROM {tabela} WHERE geometria IS NOT NULL AND geometria_wkb IS NULL"
_PREENCHER = """
    UPDATE {tabela} SET geometria_wkb = SDO_UTIL.TO_WKBGEOMETRY(geometria)
     WHERE ROWID = CHARTOROWID(:rid) AND geometria_wkb IS NULL"""


def fill_table(con, tabela: str, lote: int) -> int:
    """Preenche o WKB que falta numa tabela, com commit a cada lote. Devolve quantas linhas preencheu."""
    if tabela not in TABELAS:
        raise ValueError(f"A tabela {tabela} não tem geometria_wkb (use {', '.join(TABELAS)}).")
    with con.cursor() as cur:
        cur.arraysize = 5000
        cur.execute(_PENDENTES.format(tabela=tabela))
        rowids = [rid for (rid,) in cur.fetchall()]
        log.info("%s: %d linhas sem geometria_wkb", tabela, len(rowids))
        for inicio in range(0, len(rowids), lote):
            cur.executemany(_PREENCHER.format(tabela=tabela), [{"rid": r} for r in rowids[inicio:inicio + lote]])
            con.commit()
            log.info("%s: %d de %d", tabela, min(inicio + lote, len(rowids)), len(rowids))
    return len(rowids)


def fill(cfg: Config) -> dict:
    """Preenche as duas tabelas. Devolve quantas linhas cada uma ganhou (para o XCom)."""
    with conectar(cfg) as con:
        return {tabela: fill_table(con, tabela, cfg.tamanho_lote) for tabela in TABELAS}
