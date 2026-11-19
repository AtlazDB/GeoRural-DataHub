"""Conexão com o Oracle (python-oracledb em modo thin: não precisa de Instant Client)."""

import re
from contextlib import contextmanager

import oracledb

from georural.config import Config

# Nome de schema vai direto no SQL (ALTER SESSION não aceita bind), então só letras,
# números e _ — nada que permita injetar comando.
_IDENTIFICADOR = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")

# Linhas por ida ao banco em toda consulta (o padrão do driver é 100). A carga lê tabelas inteiras
# (o que a Tratada já tem, a fila da quarentena, os imóveis da referência cruzada): no PRODES, as
# 166 mil linhas da input_feature caíram de 7–12 s para ~2,5 s (US13.T23). Consulta de uma linha
# não muda. As DAGs de indicador já pediam 5000 no próprio cursor.
LINHAS_POR_IDA = 5000
oracledb.defaults.arraysize = LINHAS_POR_IDA
oracledb.defaults.prefetchrows = LINHAS_POR_IDA


@contextmanager
def conectar(cfg: Config):
    """Abre uma conexão e fecha ao sair do with. Nada é confirmado sem commit() explícito."""
    con = oracledb.connect(
        user=cfg.db_usuario,
        password=cfg.db_senha,
        dsn=cfg.db_dsn or f"{cfg.db_host}:{cfg.db_porta}/{cfg.db_servico}",
    )
    try:
        if cfg.db_schema:
            if not _IDENTIFICADOR.match(cfg.db_schema):
                raise RuntimeError(f"GEORURAL_DB_SCHEMA inválido: {cfg.db_schema!r}")
            # Na VM o Airflow conecta com um usuário próprio (sem DDL) e trabalha nas tabelas
            # do dono da aplicação; assim o SQL não precisa do prefixo do schema.
            with con.cursor() as cur:
                cur.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {cfg.db_schema}")
        yield con
    finally:
        con.close()
