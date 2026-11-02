"""As colunas que chegaram (fonte_coluna) e o mapeamento delas (regra_campo).

detectar_colunas registra, a cada arquivo processado, as colunas que ele trouxe: nome, tipo
percebido, até 3 exemplos e a posição. É o que a tela de mapeamento (botão #) mostra para o
operador escolher destino, tipo e regras. Coluna que sumiu continua lá, com
presente_ultimo_arquivo = 0; coluna nova aparece como "nova" até ganhar regra.

Sem nenhuma regra_campo, a fonte ainda não diz para onde os dados vão: o arquivo fica
AGUARDANDO_MAPEAMENTO e é processado de novo pelo "Processar" depois de mapear.
"""

import json
import re
from dataclasses import dataclass, field

from georural.leitura import COLUNA_GEOMETRIA, texto

AMOSTRA = 200  # registros olhados para perceber o tipo e tirar os exemplos
EXEMPLOS = 3

_DATA_BR = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_DATA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][\d:.]+(Z|[+-]\d{2}:?\d{2})?)?$")
_INTEIRO = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?\d*\.\d+$")


def tipo_percebido(valores: list) -> str:
    """O tipo que os valores (não nulos) sugerem, nos nomes da regra_campo.tipo_dado."""
    if not valores:
        return "TEXTO"
    if all(isinstance(v, bool) for v in valores):
        return "BOOLEANO"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in valores):
        return "INTEIRO"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in valores):
        return "DECIMAL"
    textos = [str(v).strip() for v in valores]
    if all(_DATA_BR.match(t) or _DATA_ISO.match(t) for t in textos):
        return "DATA"
    if all(_INTEIRO.match(t) for t in textos):
        return "INTEIRO"
    if all(_INTEIRO.match(t) or _DECIMAL.match(t) for t in textos):
        return "DECIMAL"
    return "TEXTO"


@dataclass
class Levantamento:
    """O que a primeira passada pelo arquivo descobre sobre as colunas."""
    nomes: list[str] = field(default_factory=list)  # na ordem em que apareceram
    amostra: dict[str, list] = field(default_factory=dict)
    tem_geometria: bool = False

    def acrescentar(self, atributos: dict, geometria) -> None:
        for nome, valor in atributos.items():
            valores = self.amostra.get(nome)
            if valores is None:
                self.nomes.append(nome)
                valores = self.amostra[nome] = []
            if valor is not None and len(valores) < AMOSTRA:
                valores.append(valor)
        if geometria is not None:
            self.tem_geometria = True

    def acrescentar_tabela(self, bloco) -> None:
        """O mesmo que acrescentar, para um bloco inteiro (leitura.Tabela)."""
        for nome, valores in bloco.atributos.items():
            amostra = self.amostra.get(nome)
            if amostra is None:
                self.nomes.append(nome)
                amostra = self.amostra[nome] = []
            if len(amostra) < AMOSTRA:
                amostra.extend(v for v in valores if v is not None)
                del amostra[AMOSTRA:]
        if any(g is not None for g in bloco.geometrias):
            self.tem_geometria = True


def detectar_colunas(con, fonte_id: int, levantamento: Levantamento) -> int:
    """MERGE em fonte_coluna. Devolve quantas colunas novas apareceram. Não dá commit."""
    linhas = []
    for ordem, nome in enumerate(levantamento.nomes, start=1):
        valores = levantamento.amostra[nome]
        exemplos = list(dict.fromkeys(texto(v)[:100] for v in valores))[:EXEMPLOS]
        linhas.append({"fonte_id": fonte_id, "nome": nome[:128], "tipo": tipo_percebido(valores),
                       "exemplos": json.dumps(exemplos, ensure_ascii=False) if exemplos else None,
                       "ordem": ordem})
    if levantamento.tem_geometria:
        linhas.append({"fonte_id": fonte_id, "nome": COLUNA_GEOMETRIA, "tipo": "GEOMETRIA",
                       "exemplos": None, "ordem": len(levantamento.nomes) + 1})

    with con.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM fonte_coluna WHERE fonte_id = :id", id=fonte_id)
        antes = int(cur.fetchone()[0])
        # O MERGE marca de novo as que vieram; as que sumiram ficam com 0.
        cur.execute("UPDATE fonte_coluna SET presente_ultimo_arquivo = 0 WHERE fonte_id = :id", id=fonte_id)
        cur.executemany(
            """
            MERGE INTO fonte_coluna d
            USING (SELECT :fonte_id fonte_id, :nome nome_coluna, :tipo tipo, :exemplos exemplos,
                          :ordem ordem FROM dual) s
               ON (d.fonte_id = s.fonte_id AND d.nome_coluna = s.nome_coluna)
            WHEN MATCHED THEN UPDATE SET d.tipo_detectado = s.tipo, d.exemplos = s.exemplos,
                 d.ordem = s.ordem, d.presente_ultimo_arquivo = 1, d.ultima_deteccao = SYSTIMESTAMP
            WHEN NOT MATCHED THEN INSERT (fonte_id, nome_coluna, tipo_detectado, exemplos, ordem)
                 VALUES (s.fonte_id, s.nome_coluna, s.tipo, s.exemplos, s.ordem)
            """,
            linhas,
        )
        cur.execute("SELECT COUNT(*) FROM fonte_coluna WHERE fonte_id = :id", id=fonte_id)
        return int(cur.fetchone()[0]) - antes


@dataclass
class Mapeamento:
    """O mapeamento da fonte, do jeito que o caminho genérico precisa."""
    tabelas: list[str]
    chaves: list[str]                  # colunas de origem marcadas como chave, na ordem da chave natural
    coluna_do_campo: dict[str, str]    # campo de destino -> coluna de origem
    colunas_da_fonte: list[str]        # todas as colunas já registradas da fonte, mapeadas ou não

    @property
    def tabela(self) -> str | None:
        return self.tabelas[0] if self.tabelas else None


def mapeamento(con, fonte_id: int) -> Mapeamento:
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT LOWER(rc.destino_tabela), LOWER(rc.campo_destino), fc.nome_coluna, rc.chave
              FROM regra_campo rc JOIN fonte_coluna fc ON fc.id = rc.fonte_coluna_id
             WHERE rc.fonte_id = :id
             ORDER BY fc.ordem NULLS LAST, fc.nome_coluna
            """,
            id=fonte_id,
        )
        regras = cur.fetchall()
        cur.execute("SELECT nome_coluna FROM fonte_coluna WHERE fonte_id = :id ORDER BY ordem NULLS LAST", id=fonte_id)
        nomes = [linha[0] for linha in cur.fetchall()]
    tabelas = list(dict.fromkeys(r[0] for r in regras))
    return Mapeamento(
        tabelas=tabelas,
        chaves=[r[2] for r in regras if r[3] == 1],
        coluna_do_campo={r[1]: r[2] for r in regras},
        colunas_da_fonte=nomes,
    )
