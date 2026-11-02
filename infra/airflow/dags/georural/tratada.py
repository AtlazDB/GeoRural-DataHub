"""Gravação direto na Zona Tratada, sem staging, só do que mudou (US11.T04).

Era o pkg_tratada.gravar_aprovados (MERGE ou INSERT a partir da staging). Cada linha aprovada
ganha um hash (sha1 dos valores mapeados, já no tipo do destino, mais a geometria em WKB; coluna
hash_registro, V21 do back-end) e é comparada com o que a tabela já tem:

    com chave                    chave nova entra; chave com outro hash é atualizada; igual fica
                                 como está. Nada é apagado (como o MERGE).
    sem chave, com cod_imovel    carga-retrato por imóvel (a Reserva Legal): para cada imóvel que
                                 veio, entra o polígono novo e sai o que não veio mais.
    sem chave nem cod_imovel     carga-retrato do conjunto (dataset_versao.conjunto): o mesmo, no
                                 conjunto inteiro.

O que não mudou continua com a versão (dataset_versao_id) da carga que o gravou: a versão de uma
linha é a da última carga que mexeu nela.

A gravação vai em blocos, com commit em cada um (o undo do Autonomous tem 1 GB). Se a carga
falhar no meio, o que já foi gravado fica certo, e a próxima carga só refaz o que falta: o que já
entrou tem o mesmo hash e é pulado.
"""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import oracledb
import shapely

from georural import sdo
from georural.regras import Regra, campos_derivados, converter



class FaltaMigration(RuntimeError):
    """A tabela de destino ainda não tem a coluna hash_registro (V21 do back-end)."""


def canonico(valor):
    """O valor num texto único, o mesmo vindo do Python ou do banco: entra no hash e na chave."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return str(int(valor))
    if isinstance(valor, float):
        valor = Decimal(repr(valor))
    if isinstance(valor, Decimal):
        texto = format(valor.normalize(), "f")
        return "0" if texto in ("-0", "0") else texto
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return str(valor)


def hash_registro(valores: dict, geometria_wkb: bytes | None) -> str:
    sha = hashlib.sha1(json.dumps({k: canonico(v) for k, v in sorted(valores.items())},
                                  ensure_ascii=False, sort_keys=True).encode("utf-8"))
    sha.update(b"|")
    sha.update(geometria_wkb or b"")
    return sha.hexdigest()


@dataclass
class Contagem:
    inseridos: int = 0
    alterados: int = 0
    iguais: int = 0
    removidos: int = 0

    def somar(self, outra: "Contagem") -> None:
        self.inseridos += outra.inseridos
        self.alterados += outra.alterados
        self.iguais += outra.iguais
        self.removidos += outra.removidos


@dataclass
class Destino:
    """Uma tabela de destino da fonte e como gravar nela."""
    tabela: str
    regras: list[Regra]                 # na ordem do pkg_tratada.montar: chave primeiro, depois por id
    colunas: set[str]                   # colunas que a tabela tem
    modo: str = ""                      # chave | imovel | conjunto
    existentes: dict = field(default_factory=dict)
    vistos: dict = field(default_factory=lambda: defaultdict(list))

    @property
    def campos(self) -> list[str]:
        """Os campos gravados, iguais em todas as linhas: um por regra (fora a geometria), mais o
        estado deduzido do cod_imovel ou do cod_ibge quando a tabela o tem e ele não é mapeado."""
        mapeados = [r.campo for r in self.regras if r.tipo != "GEOMETRIA"]
        deduzido = {"imovel_rural": "cod_imovel", "municipio": "cod_ibge"}.get(self.tabela)
        if deduzido in mapeados and "estado" not in mapeados and "estado" in self.colunas:
            mapeados.append("estado")
        return [c for c in mapeados if c in self.colunas]

    @property
    def chaves(self) -> list[Regra]:
        return [r for r in self.regras if r.chave]

    @property
    def geometria(self) -> Regra | None:
        return next((r for r in self.regras if r.tipo == "GEOMETRIA"), None)


def _numeros_exatos(cursor, metadado):
    """NUMBER vem como Decimal: a chave e o hash comparam o número exato."""
    if metadado.type_code is oracledb.DB_TYPE_NUMBER:
        return cursor.var(Decimal, arraysize=cursor.arraysize)


class Tratada:
    """Grava os aprovados de uma carga nas tabelas de destino da fonte."""

    def __init__(self, con, regras: list[Regra], versao_id: int, conjunto: str | None, carregar: bool = True):
        """`carregar`: lê o que as tabelas já têm, para a carga comparar; o "Revalidar" (gravar_um),
        que mexe numa linha só, não precisa."""
        self.con = con
        self.versao_id = versao_id
        self.conjunto = conjunto
        # A geometria vai como objeto SDO_GEOMETRY montado aqui: bytes (WKB) passam como RAW no modo
        # thin, com limite de 32 KB, e os polígonos grandes chegariam cortados (ver sdo.py).
        self.tipos = sdo.TiposSdo(con)
        tabelas = list(dict.fromkeys(r.tabela for r in regras))
        self.destinos: list[Destino] = []
        with con.cursor() as cur:
            for tabela in tabelas:
                cur.execute("SELECT LOWER(column_name) FROM user_tab_columns WHERE table_name = UPPER(:t)", t=tabela)
                colunas = {c for (c,) in cur}
                if "hash_registro" not in colunas:
                    raise FaltaMigration(f"A tabela {tabela} não tem a coluna hash_registro: aplique as migrations "
                                         "do back-end (V21) antes de processar.")
                regras_tabela = sorted((r for r in regras if r.tabela == tabela), key=lambda r: (not r.chave, r.id))
                destino = Destino(tabela, regras_tabela, colunas)
                destino.modo = ("chave" if destino.chaves else
                                "imovel" if any(r.campo == "cod_imovel" for r in regras_tabela) else "conjunto")
                if carregar:
                    self._carregar(cur, destino)
                self.destinos.append(destino)

    # -------------------------------------------------------------- o que a tabela já tem

    def _filtro_conjunto(self, destino: Destino) -> str:
        if "dataset_versao_id" in destino.colunas and self.conjunto:
            return " WHERE dataset_versao_id IN (SELECT id FROM dataset_versao WHERE conjunto = :conjunto)"
        return ""

    def _carregar(self, cur, destino: Destino) -> None:
        cur.outputtypehandler = _numeros_exatos
        tabela = destino.tabela
        if destino.modo == "chave":
            colunas = ", ".join(r.campo for r in destino.chaves)
            # Na tabela genérica, a chave só vale dentro do conjunto da fonte: uma fonte não mexe na outra.
            filtro = self._filtro_conjunto(destino) if tabela == "input_feature" else ""
            cur.execute(f"SELECT {colunas}, ROWIDTOCHAR(ROWID), hash_registro FROM {tabela}{filtro}",
                        **({"conjunto": self.conjunto} if filtro else {}))
            n = len(destino.chaves)
            destino.existentes = {tuple(canonico(v) for v in linha[:n]): (linha[n], linha[n + 1]) for linha in cur}
        elif destino.modo == "imovel":
            cur.execute(f"SELECT cod_imovel, ROWIDTOCHAR(ROWID), hash_registro FROM {tabela}")
            por_imovel = defaultdict(list)
            for cod_imovel, rowid, hash_ in cur:
                por_imovel[cod_imovel].append((rowid, hash_))
            destino.existentes = por_imovel
        else:
            filtro = self._filtro_conjunto(destino)
            cur.execute(f"SELECT ROWIDTOCHAR(ROWID), hash_registro FROM {tabela}{filtro}",
                        **({"conjunto": self.conjunto} if filtro else {}))
            destino.existentes = {None: [(rowid, hash_) for rowid, hash_ in cur]}
        cur.outputtypehandler = None

    # -------------------------------------------------------------- gravação

    def linha(self, destino: Destino, textos: dict, geometria) -> dict:
        """Os valores de uma linha aprovada para a tabela, já no tipo do destino, com o hash."""
        convertidos = {r.campo: converter(textos.get(r.coluna), r) for r in destino.regras if r.tipo != "GEOMETRIA"}
        convertidos.update(campos_derivados(destino.tabela, convertidos))
        valores = {campo: convertidos.get(campo) for campo in destino.campos}
        geometria = geometria if destino.geometria is not None else None
        return {"valores": valores, "geometria": geometria, "hash": hash_registro(valores, wkb(geometria))}

    def gravar(self, linhas_por_destino: dict[str, list[dict]]) -> Contagem:
        """Grava um bloco e dá commit."""
        contagem = Contagem()
        with self.con.cursor() as cur:
            for destino in self.destinos:
                contagem.somar(self._gravar(cur, destino, linhas_por_destino.get(destino.tabela, [])))
        self.con.commit()
        return contagem

    def _gravar(self, cur, destino: Destino, linhas: list[dict]) -> Contagem:
        contagem = Contagem()
        inserir, alterar = [], []
        for linha in linhas:
            if destino.modo == "chave":
                chave = tuple(canonico(linha["valores"].get(r.campo)) for r in destino.chaves)
                anterior = destino.existentes.get(chave)
                if anterior is None:
                    inserir.append(linha)
                    destino.existentes[chave] = (None, linha["hash"])  # chave repetida já foi reprovada
                elif anterior[1] == linha["hash"]:
                    contagem.iguais += 1
                else:
                    alterar.append((anterior[0], linha))
            else:
                grupo = linha["valores"].get("cod_imovel") if destino.modo == "imovel" else None
                anteriores = destino.existentes.get(grupo, [])
                igual = next((i for i, (_, h) in enumerate(anteriores) if h == linha["hash"]), None)
                if igual is None:
                    inserir.append(linha)
                else:
                    destino.vistos[grupo].append(anteriores.pop(igual))
                    contagem.iguais += 1
                destino.vistos.setdefault(grupo, [])
        if inserir:
            self._inserir(cur, destino, inserir)
            contagem.inseridos += len(inserir)
        if alterar:
            self._alterar(cur, destino, alterar)
            contagem.alterados += len(alterar)
        return contagem

    def _campos(self, destino: Destino, linha: dict) -> list[str]:
        campos = list(linha["valores"])
        if "dataset_versao_id" in destino.colunas and "dataset_versao_id" not in campos:
            campos.append("dataset_versao_id")
        return campos

    def _binds(self, destino: Destino, linha: dict, campos: list[str]) -> dict:
        binds = {f"v{i}": (self.versao_id if c == "dataset_versao_id" and c not in linha["valores"]
                           else linha["valores"][c]) for i, c in enumerate(campos)}
        binds["geometria"] = self.tipos.converter(linha["geometria"])
        binds["hash_registro"] = linha["hash"]
        return binds

    def _inserir(self, cur, destino: Destino, linhas: list[dict]) -> None:
        campos = self._campos(destino, linhas[0])
        colunas = campos + (["geometria"] if destino.geometria else []) + ["hash_registro"]
        valores = [f":v{i}" for i in range(len(campos))] \
            + ([":geometria"] if destino.geometria else []) + [":hash_registro"]
        sql = f"INSERT INTO {destino.tabela} ({', '.join(colunas)}) VALUES ({', '.join(valores)})"
        if destino.geometria:
            cur.setinputsizes(geometria=self.tipos.geometria)
        cur.executemany(sql, [self._sem_geometria(destino, self._binds(destino, l, campos)) for l in linhas])

    def _alterar(self, cur, destino: Destino, linhas: list[tuple[str, dict]]) -> None:
        campos = [c for c in self._campos(destino, linhas[0][1]) if c not in {r.campo for r in destino.chaves}]
        atribuicoes = [f"{c} = :v{i}" for i, c in enumerate(campos)] \
            + (["geometria = :geometria"] if destino.geometria else []) \
            + ["hash_registro = :hash_registro"]
        sql = f"UPDATE {destino.tabela} SET {', '.join(atribuicoes)} WHERE ROWID = CHARTOROWID(:rid)"
        if destino.geometria:
            cur.setinputsizes(geometria=self.tipos.geometria)
        cur.executemany(sql, [{**self._sem_geometria(destino, self._binds(destino, l, campos)), "rid": rowid}
                              for rowid, l in linhas])

    @staticmethod
    def _sem_geometria(destino: Destino, binds: dict) -> dict:
        if destino.geometria is None:
            binds.pop("geometria")
        return binds

    def gravar_um(self, cur, destino: Destino, linha: dict) -> bool:
        """Grava uma linha sem dar commit, como a carga: com chave, atualiza a linha da chave se
        houver; sem chave, é mais uma linha da carga. Devolve True se a linha é nova na tabela."""
        if destino.chaves:
            condicoes = " AND ".join(f"{r.campo} = :k{i}" for i, r in enumerate(destino.chaves))
            binds = {f"k{i}": linha["valores"].get(r.campo) for i, r in enumerate(destino.chaves)}
            if destino.tabela == "input_feature" and self.conjunto:
                condicoes += " AND dataset_versao_id IN (SELECT id FROM dataset_versao WHERE conjunto = :conjunto)"
                binds["conjunto"] = self.conjunto
            cur.execute(f"SELECT ROWIDTOCHAR(ROWID) FROM {destino.tabela} WHERE {condicoes}", **binds)
            achada = cur.fetchone()
            if achada:
                self._alterar(cur, destino, [(achada[0], linha)])
                return False
        self._inserir(cur, destino, [linha])
        return True

    def finalizar(self) -> Contagem:
        """Na carga-retrato, apaga o que não veio mais. Não dá commit (vai junto com a conclusão)."""
        contagem = Contagem()
        with self.con.cursor() as cur:
            for destino in self.destinos:
                if destino.modo == "chave":
                    continue
                grupos = destino.vistos.keys() if destino.modo == "imovel" else [None]
                sobras = [rowid for g in grupos for rowid, _ in destino.existentes.get(g, [])]
                if sobras:
                    cur.executemany(f"DELETE FROM {destino.tabela} WHERE ROWID = CHARTOROWID(:rid)",
                                    [{"rid": r} for r in sobras])
                    contagem.removidos += len(sobras)
        return contagem


def wkb(geometria) -> bytes | None:
    """A geometria final em WKB 2D (já normalizada pela leitura, ou a corrigida): entra no hash."""
    if geometria is None or geometria.is_empty:
        return None
    if geometria.geom_type in ("Polygon", "MultiPolygon"):
        geometria = shapely.orient_polygons(geometria, exterior_cw=False)
    return shapely.to_wkb(geometria, output_dimension=2)
