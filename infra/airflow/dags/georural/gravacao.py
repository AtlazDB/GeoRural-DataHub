"""Toda a escrita do processamento no Oracle, em SQL direto — o mesmo SQL do GravadorImoveis
do back-end. Usa as tabelas como estão hoje (Flyway V1 a V7).

A gravação dos aprovados ainda é o MERGE daqui; a quarentena já sai completa (tabela de
destino, chave, tipo de erro, versão e geometria), que é o que o "Revalidar" da tela precisa
para o "Revalidar" gravar o registro depois.
"""

import json
from dataclasses import dataclass, field

import oracledb
import shapely

from georural import sdo
from georural.tratada import FaltaMigration, hash_registro, wkb
from georural.validacao import Resultado

CONJUNTO_IMOVEIS = "imoveis_rurais"
# O que os dois caminhos sabem ler (ver leitura.py): zip de shapefile ou de GeoJSON, ou GeoJSON.
EXTENSOES_PROCESSAVEIS = (".zip", ".geojson", ".json")
# Conjunto de dados (dataset_versao.conjunto) pela tabela de destino: o IFC, o IRL e a
# Rastreabilidade procuram as versões por estes nomes.
CONJUNTO_DA_TABELA = {
    "imovel_rural": CONJUNTO_IMOVEIS,
    "car_reserva_legal": "reserva_legal",
    "foco_calor": "focos_calor",
    "municipio": "municipios",
}
# Tabela genérica das fontes novas (V20): cada fonte é um conjunto, "insumo_<sigla>". Na
# carga-retrato o pkg_tratada.gravar_aprovados apaga as versões anteriores do conjunto: com o
# nome da tabela como conjunto, a carga de uma fonte apagaria os dados de todas as outras.
TABELA_INSUMO = "input_feature"
# Tabela de destino gravada na quarentena (registro_quarentena.entidade), a mesma da regra_campo
ENTIDADE_IMOVEIS = "imovel_rural"
# Quem aparece como autor da análise nos registros que a própria DAG descarta. O começo
# ("DAG processar_arquivo") é o que o substituir_pendentes procura para
# encerrar esses descartes quando uma carga mais nova decide de novo: não mude sem mudar lá.
AUTOR_DESCARTE_AUTOMATICO = "DAG processar_arquivo (desempate de código repetido)"

SQL_MERGE_IMOVEL = """
    MERGE INTO imovel_rural d
    USING (SELECT :cod_imovel cod_imovel, :municipio municipio, :estado estado, :area_ha area_ha,
                  :situacao situacao, :geometria geometria,
                  :dataset_versao_id dataset_versao_id FROM dual) s
    ON (d.cod_imovel = s.cod_imovel)
    WHEN MATCHED THEN UPDATE SET d.municipio = s.municipio, d.estado = s.estado,
         d.area_ha = s.area_ha, d.situacao = s.situacao, d.geometria = s.geometria,
         d.dataset_versao_id = s.dataset_versao_id
    WHEN NOT MATCHED THEN INSERT (cod_imovel, municipio, estado, area_ha, situacao,
         geometria, dataset_versao_id)
         VALUES (s.cod_imovel, s.municipio, s.estado, s.area_ha, s.situacao,
                 s.geometria, s.dataset_versao_id)
"""

SQL_QUARENTENA = """
    INSERT INTO registro_quarentena (execucao_id, numero_linha, campo, motivo, payload,
           entidade, chave_natural, tipo_erro, valor_recebido, dataset_versao_id, geometria,
           hash_registro, status_analise, justificativa, analisado_por, analisado_em)
    VALUES (:execucao_id, :numero_linha, :campo, :motivo, :payload,
            :entidade, :chave_natural, :tipo_erro, :valor_recebido, :dataset_versao_id, :geometria,
            :hash_registro, :status_analise, :justificativa, :analisado_por,
            CASE WHEN :descartado = 1 THEN SYSTIMESTAMP END)
"""


@dataclass(frozen=True)
class Arquivo:
    id: int
    chave_objeto: str
    nome_original: str
    status: str
    sigla_fonte: str

    @property
    def caminho_car(self) -> bool:
        """O shapefile de imóveis da fonte CAR (.zip) segue o caminho próprio (car.py), com as
        regras em Python. O resto vai pelo genérico (generico.py), guiado pelo mapeamento."""
        return self.sigla_fonte.upper() == "CAR" and self.nome_original.lower().endswith(".zip")

    @property
    def processavel(self) -> bool:
        return self.nome_original.lower().endswith(EXTENSOES_PROCESSAVEIS)


@dataclass(frozen=True)
class Versao:
    id: int
    numero: int


# ------------------------------------------------------------------ arquivo e reserva

def buscar_arquivo(con, arquivo_id: int) -> Arquivo | None:
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT a.id, a.chave_objeto, a.nome_original, a.status, f.sigla
              FROM arquivo_bruto a JOIN fonte f ON f.id = a.fonte_id
             WHERE a.id = :id
            """,
            id=arquivo_id,
        )
        linha = cur.fetchone()
    return Arquivo(*linha) if linha else None


def listar_recebidos(con, limite: int) -> list[int]:
    """Arquivos que chegaram na zona bruta (upload ou coleta) e ainda não foram processados, de
    qualquer fonte, nos formatos que o processamento lê."""
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT a.id
              FROM arquivo_bruto a
             WHERE a.status = 'RECEBIDO'
               AND (LOWER(a.nome_original) LIKE '%.zip'
                    OR LOWER(a.nome_original) LIKE '%.geojson'
                    OR LOWER(a.nome_original) LIKE '%.json')
             ORDER BY a.recebido_em
             FETCH FIRST :limite ROWS ONLY
            """,
            limite=limite,
        )
        return [linha[0] for linha in cur.fetchall()]


def reservar(con, arquivo_id: int, somente_recebido: bool = True) -> bool:
    """Passa o arquivo para PROCESSANDO. Devolve False se outro processo pegou antes.

    O UPDATE condicional é o que impede dois processamentos do mesmo arquivo: só um consegue
    mudar o status. Com somente_recebido=False aceita reprocessar um arquivo já processado
    ou rejeitado (disparo manual), mas nunca um que esteja PROCESSANDO.
    """
    condicao = "status = 'RECEBIDO'" if somente_recebido else "status <> 'PROCESSANDO'"
    with con.cursor() as cur:
        cur.execute(
            f"UPDATE arquivo_bruto SET status = 'PROCESSANDO', processado_em = NULL "
            f"WHERE id = :id AND {condicao}",
            id=arquivo_id,
        )
        reservado = cur.rowcount == 1
    con.commit()
    return reservado


# ------------------------------------------------------------------ execução e versão

def iniciar_execucao(con, arquivo_id: int, dag_run_id: str | None) -> int:
    """Abre a execução fora da transação principal: se o resto falhar, ela continua no banco
    para receber FALHOU."""
    with con.cursor() as cur:
        id_var = cur.var(oracledb.DB_TYPE_NUMBER)
        cur.execute(
            """
            INSERT INTO execucao (arquivo_bruto_id, tipo, status, dag_run_id)
            VALUES (:arquivo_id, 'VALIDACAO', 'EM_ANDAMENTO', :dag_run_id)
            RETURNING id INTO :id_saida
            """,
            arquivo_id=arquivo_id,
            dag_run_id=(dag_run_id or "")[:120] or None,
            id_saida=id_var,
        )
        execucao_id = int(id_var.getvalue()[0])
    con.commit()
    return execucao_id


def conjunto_da_carga(tabela: str, sigla_fonte: str) -> str:
    """O conjunto (dataset_versao.conjunto) da carga: por fonte na input_feature, pela tabela nas demais."""
    if tabela == TABELA_INSUMO:
        return f"insumo_{sigla_fonte.lower()}"
    return CONJUNTO_DA_TABELA.get(tabela, tabela)


def criar_versao(cur, execucao_id: int, conjunto: str = CONJUNTO_IMOVEIS) -> Versao:
    """Próxima versão do conjunto na zona TRATADA. Roda dentro da transação principal."""
    cur.execute(
        "SELECT NVL(MAX(versao), 0) + 1 FROM dataset_versao WHERE conjunto = :conjunto",
        conjunto=conjunto,
    )
    numero = int(cur.fetchone()[0])
    id_var = cur.var(oracledb.DB_TYPE_NUMBER)
    cur.execute(
        """
        INSERT INTO dataset_versao (conjunto, versao, execucao_id, zona)
        VALUES (:conjunto, :versao, :execucao_id, 'TRATADA')
        RETURNING id INTO :id_saida
        """,
        conjunto=conjunto,
        versao=numero,
        execucao_id=execucao_id,
        id_saida=id_var,
    )
    return Versao(id=int(id_var.getvalue()[0]), numero=numero)


# ------------------------------------------------------------------ lotes

def gravar_imoveis(cur, dataset_versao_id: int, aprovados: list[Resultado]) -> None:
    """Grava um lote de aprovados, com a geometria já montada como SDO_GEOMETRY (ver sdo.py)."""
    if not aprovados:
        return
    tipos = sdo.TiposSdo(cur.connection)
    cur.setinputsizes(geometria=tipos.geometria)
    cur.executemany(
        SQL_MERGE_IMOVEL,
        [
            {
                "cod_imovel": r.cod_imovel,
                "municipio": r.municipio,
                "estado": r.estado,
                "area_ha": r.area_ha,
                "situacao": r.situacao,
                "geometria": tipos.converter(r.geometria, sdo.SRID_BANCO),
                "dataset_versao_id": dataset_versao_id,
            }
            for r in aprovados
        ],
    )


def gravar_quarentena(cur, execucao_id: int, dataset_versao_id: int, reprovados: list[Resultado],
                      fila: "FilaQuarentena") -> None:
    novos = fila.so_os_novos(cur, execucao_id, dataset_versao_id, [
        (hash_quarentena(r.payload_json(), r.campo, r.tipo_erro, r.motivo, r.geometria_recebida), r.linha, r)
        for r in reprovados
    ])
    if not novos:
        return
    tipos = sdo.TiposSdo(cur.connection)
    cur.setinputsizes(payload=oracledb.DB_TYPE_CLOB, geometria=tipos.geometria)
    cur.executemany(
        SQL_QUARENTENA,
        [
            {
                "execucao_id": execucao_id,
                "numero_linha": r.linha,
                "campo": r.campo,
                "motivo": r.motivo[:400],
                "payload": r.payload_json(),
                "entidade": ENTIDADE_IMOVEIS,
                "chave_natural": (r.cod_imovel or "")[:100] or None,
                "tipo_erro": r.tipo_erro,
                "valor_recebido": r.valor_recebido,
                "dataset_versao_id": dataset_versao_id,
                "geometria": geometria_quarentena(tipos, r.geometria_recebida),
                "hash_registro": hash_,
                "status_analise": "DESCARTADO" if r.descartado_automaticamente else "PENDENTE",
                "justificativa": r.justificativa if r.descartado_automaticamente else None,
                "analisado_por": AUTOR_DESCARTE_AUTOMATICO if r.descartado_automaticamente else None,
                "descartado": 1 if r.descartado_automaticamente else 0,
            }
            for hash_, r in novos
        ],
    )


def geometria_quarentena(tipos: sdo.TiposSdo, geometria):
    """A geometria como chegou, para o visualizador e o "Revalidar". Só polígonos (os outros
    tipos o sdo.py não converte: ficam sem geometria). Vai no sentido que o Oracle exige, para
    um registro reprovado por outro motivo (código duplicado) poder ser gravado depois."""
    if geometria is None or geometria.is_empty or geometria.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    return tipos.converter(shapely.orient_polygons(geometria, exterior_cw=False), sdo.SRID_BANCO)


# ------------------------------------------------------------------ fila da quarentena

def hash_quarentena(payload: str | None, campo: str | None, tipo_erro: str | None, motivo: str | None, geometria) -> str:
    """O que identifica um registro reprovado: o que veio no arquivo e por que foi reprovado. O
    motivo entra porque, se a regra mudar, o analista precisa ver o registro de novo."""
    atributos = json.dumps(json.loads(payload), ensure_ascii=False, sort_keys=True) if payload else None
    return hash_registro({"payload": atributos, "campo": campo, "tipo_erro": tipo_erro, "motivo": motivo},
                         wkb(geometria))


@dataclass
class FilaQuarentena:
    """O que as cargas anteriores deixaram na quarentena, por hash. Um reprovado igual não é gravado
    de novo: se ainda está em aberto, o registro passa para esta carga (e não é substituído); se o
    analista já decidiu, a decisão continua valendo.

    `por_hash`: para cada hash, o id de cada registro em aberto, ou None se a análise já acabou."""
    por_hash: dict[str, list[int | None]] = field(default_factory=dict)
    iguais: int = 0

    def so_os_novos(self, cur, execucao_id: int, versao_id: int, candidatos: list[tuple]) -> list[tuple]:
        """`candidatos`: (hash, número da linha, registro). Devolve (hash, registro) do que falta gravar."""
        novos, abertos = [], []
        for hash_, numero_linha, registro in candidatos:
            anteriores = self.por_hash.get(hash_)
            if not anteriores:  # lista vazia: linhas idênticas no arquivo, cada uma é um registro
                novos.append((hash_, registro))
                continue
            self.iguais += 1
            id_aberto = anteriores.pop()
            if id_aberto is not None:
                abertos.append({"id": id_aberto, "execucao": execucao_id, "versao": versao_id, "linha": numero_linha})
        if abertos:
            cur.executemany("""UPDATE registro_quarentena
                                  SET execucao_id = :execucao, dataset_versao_id = :versao, numero_linha = :linha
                                WHERE id = :id""", abertos)
        return novos


def carregar_fila(cur, conjunto: str, com_chave: bool) -> FilaQuarentena:
    """Os registros do conjunto com hash, menos os que uma carga já substituiu.

    Revalidado só conta com chave: na carga-retrato, o registro revalidado não vem no arquivo e é
    apagado da Tratada, então o reprovado precisa voltar para a fila, ou o dado sumiria sem aviso."""
    try:
        cur.execute("""SELECT q.id, q.hash_registro, q.status_analise, q.analisado_por FROM registro_quarentena q
                        WHERE q.hash_registro IS NOT NULL AND q.status_analise <> 'SUBSTITUIDO'
                          AND q.execucao_id IN (SELECT dv.execucao_id FROM dataset_versao dv
                                                 WHERE dv.conjunto = :conjunto)""", conjunto=conjunto)
        linhas = cur.fetchall()
    except oracledb.DatabaseError as e:
        if "ORA-00904" in str(e):
            raise FaltaMigration("A tabela registro_quarentena não tem a coluna hash_registro: aplique as "
                                 "migrations do back-end (V23).") from e
        raise
    fila = FilaQuarentena()
    for id_, hash_, status, autor in linhas:
        if status == "REVALIDADO" and not com_chave:
            continue
        encerrado = status == "REVALIDADO" or (status == "DESCARTADO" and autor != AUTOR_DESCARTE_AUTOMATICO)
        fila.por_hash.setdefault(hash_, []).append(None if encerrado else id_)
    return fila


# Quem aparece como autor quando a carga encerra um registro antigo da quarentena.
AUTOR_SUBSTITUICAO = "Carga (substituir_pendentes)"
# Registro que ainda pode ser substituído: em aberto, ou descartado pela própria DAG (uma carga mais
# nova decide de novo). Descarte feito por uma pessoa não muda.
_EM_ABERTO = """(q.status_analise IN ('PENDENTE', 'EM_ANALISE')
                 OR (q.status_analise = 'DESCARTADO' AND q.analisado_por LIKE :automatico))"""
_SUBSTITUIR = """UPDATE registro_quarentena q
                    SET q.status_analise = 'SUBSTITUIDO', q.justificativa = :motivo,
                        q.analisado_por = :autor, q.analisado_em = SYSTIMESTAMP"""


@dataclass
class Retrato:
    """Uma carga sem chave: ela substitui o que veio antes. `coluna_imovel`: a coluna do cod_imovel
    na fonte, quando a carga é um retrato por imóvel (a Reserva Legal), e `imoveis`, os que vieram
    nela; sem a coluna, o retrato é do conjunto inteiro."""
    coluna_imovel: str | None = None
    imoveis: set[str] = field(default_factory=set)

    def substitui(self, payload: str | None) -> bool:
        """Esta carga substitui o registro antigo da quarentena com este payload?"""
        if self.coluna_imovel is None:
            return True
        valor = json.loads(payload).get(self.coluna_imovel) if payload else None
        return isinstance(valor, str) and valor.strip() in self.imoveis


def substituir_pendentes(cur, execucao_id: int, chaves_aprovadas: set[str] | None = None,
                         retrato: Retrato | None = None) -> int:
    """Encerra como SUBSTITUIDO o que esta carga resolveu, para reprocessar não duplicar a fila:

        1. o mesmo arquivo processado de novo: o que ficou aberto das execuções anteriores;
        2. outro arquivo, de versão anterior do conjunto, com um código que esta carga reprovou de
           novo (o problema agora está no registro novo) ou aprovou;
        3. na carga sem chave (`retrato`), o que veio de versões anteriores do mesmo imóvel, ou do
           conjunto: o registro antigo não tem código para comparar, e a carga trouxe o retrato novo.
           Sem isso, cada carga da Reserva Legal repetia na fila os mesmos registros reprovados.

    `chaves_aprovadas`: as chaves naturais aprovadas nesta carga. Na carga incremental (generico.py)
    é preciso passar: a linha igual não é regravada e fica com a versão antiga, então a tabela de
    destino não diz o que esta carga aprovou. Sem ela (car.py, que regrava tudo), olha a tabela.
    """
    cur.execute("""SELECT e.arquivo_bruto_id, a.fonte_id FROM execucao e
                     JOIN arquivo_bruto a ON a.id = e.arquivo_bruto_id WHERE e.id = :e""", e=execucao_id)
    arquivo_id, fonte_id = cur.fetchone()
    cur.execute("SELECT MAX(id) FROM dataset_versao WHERE execucao_id = :e", e=execucao_id)
    versao_id = cur.fetchone()[0]
    numero, conjunto = None, None
    if versao_id is not None:
        cur.execute("SELECT versao, conjunto FROM dataset_versao WHERE id = :id", id=versao_id)
        numero, conjunto = cur.fetchone()

    de_versao = f" (versão {numero} de {conjunto})" if numero is not None else ""
    binds = {"motivo": f"Substituído pela carga da execução {execucao_id}{de_versao}, que tratou este registro de novo.",
             "autor": AUTOR_SUBSTITUICAO, "automatico": "DAG processar_arquivo%"}

    cur.execute(f"""{_SUBSTITUIR} WHERE {_EM_ABERTO}
                      AND q.execucao_id IN (SELECT e.id FROM execucao e
                                             WHERE e.arquivo_bruto_id = :arquivo AND e.id <> :execucao)""",
                arquivo=arquivo_id, execucao=execucao_id, **binds)
    total = cur.rowcount
    if conjunto is None:
        return total  # carga sem versão de dados: não há "mais nova" para comparar

    anteriores = """q.execucao_id IN (SELECT dv.execucao_id FROM dataset_versao dv
                                       WHERE dv.conjunto = :conjunto AND dv.versao < :versao)"""
    cur.execute(f"""{_SUBSTITUIR} WHERE {_EM_ABERTO} AND q.chave_natural IS NOT NULL AND {anteriores}
                      AND EXISTS (SELECT 1 FROM registro_quarentena n
                                   WHERE n.execucao_id = :execucao AND n.chave_natural = q.chave_natural)""",
                conjunto=conjunto, versao=numero, execucao=execucao_id, **binds)
    total += cur.rowcount

    if retrato is not None:
        total += _substituir_no_retrato(cur, retrato, anteriores, conjunto, numero, binds)

    if chaves_aprovadas is not None:
        cur.execute(f"""SELECT q.id, q.chave_natural FROM registro_quarentena q
                         WHERE {_EM_ABERTO} AND q.chave_natural IS NOT NULL AND {anteriores}""",
                    automatico=binds["automatico"], conjunto=conjunto, versao=numero)
        resolvidos = [{"id": i, "motivo": binds["motivo"], "autor": binds["autor"]}
                      for i, chave in cur.fetchall() if chave in chaves_aprovadas]
        if resolvidos:
            cur.executemany("""UPDATE registro_quarentena
                                  SET status_analise = 'SUBSTITUIDO', justificativa = :motivo,
                                      analisado_por = :autor, analisado_em = SYSTIMESTAMP
                                WHERE id = :id""", resolvidos)
        return total + len(resolvidos)

    # A chave montada como a chave_natural: as colunas de chave na ordem da fonte, unidas por "|".
    # Se a formatação não bater (chave numérica ou data), o registro fica pendente: nunca é
    # encerrado por engano.
    cur.execute("""SELECT LOWER(rc.destino_tabela), LOWER(rc.campo_destino) FROM regra_campo rc
                     JOIN fonte_coluna fc ON fc.id = rc.fonte_coluna_id
                    WHERE rc.fonte_id = :fonte AND rc.chave = 1
                    ORDER BY fc.ordem NULLS LAST, fc.nome_coluna""", fonte=fonte_id)
    colunas_por_tabela: dict[str, list[str]] = {}
    for tabela, campo in cur.fetchall():
        colunas_por_tabela.setdefault(tabela, []).append(campo)
    for tabela, campos in colunas_por_tabela.items():
        chave = " || '|' || ".join(f"TO_CHAR(d.{c})" for c in campos)
        cur.execute(f"""{_SUBSTITUIR} WHERE {_EM_ABERTO} AND q.chave_natural IS NOT NULL
                          AND LOWER(q.entidade) = :tabela AND {anteriores}
                          AND EXISTS (SELECT 1 FROM {tabela} d
                                       WHERE {chave} = q.chave_natural AND d.dataset_versao_id = :versao_id)""",
                    tabela=tabela, conjunto=conjunto, versao=numero, versao_id=versao_id, **binds)
        total += cur.rowcount
    return total


def _substituir_no_retrato(cur, retrato: Retrato, anteriores: str, conjunto: str, numero: int, binds: dict) -> int:
    cur.execute(f"""SELECT q.id, q.payload FROM registro_quarentena q
                     WHERE {_EM_ABERTO} AND q.chave_natural IS NULL AND {anteriores}""",
                automatico=binds["automatico"], conjunto=conjunto, versao=numero)
    substituidos = [{"id": i, "motivo": binds["motivo"], "autor": binds["autor"]} for i, payload in cur.fetchall()
                    if retrato.substitui(payload.read() if hasattr(payload, "read") else payload)]
    if substituidos:
        cur.executemany("""UPDATE registro_quarentena
                              SET status_analise = 'SUBSTITUIDO', justificativa = :motivo,
                                  analisado_por = :autor, analisado_em = SYSTIMESTAMP
                            WHERE id = :id""", substituidos)
    return len(substituidos)


# ------------------------------------------------------------------ fim

def concluir(cur, arquivo_id: int, execucao_id: int, versao: Versao,
             lidos: int, validos: int, invalidos: int, chaves_aprovadas: set[str] | None = None,
             retrato: "Retrato | None" = None) -> int:
    """Fecha a carga. Devolve quantos registros antigos da quarentena ela substituiu
    (substituir_pendentes: reprocessar não duplica a fila)."""
    cur.execute(
        """
        UPDATE execucao SET status = 'CONCLUIDA', finalizada_em = SYSTIMESTAMP,
               registros_lidos = :lidos, registros_validos = :validos, registros_invalidos = :invalidos
         WHERE id = :id
        """,
        lidos=lidos, validos=validos, invalidos=invalidos, id=execucao_id,
    )
    cur.execute("UPDATE dataset_versao SET registros = :n WHERE id = :id", n=validos, id=versao.id)
    substituidos = substituir_pendentes(cur, execucao_id, chaves_aprovadas, retrato)
    cur.execute(
        "UPDATE arquivo_bruto SET status = 'PROCESSADO', processado_em = SYSTIMESTAMP WHERE id = :id",
        id=arquivo_id,
    )
    return substituidos


def aguardando_mapeamento(con, arquivo_id: int, execucao_id: int, mensagem: str) -> None:
    """A fonte ainda não tem mapeamento: as colunas ficaram registradas e o arquivo espera o
    operador mapear e mandar processar de novo. Não é falha: nada foi gravado na Tratada."""
    with con.cursor() as cur:
        cur.execute(
            """
            UPDATE execucao SET status = 'CONCLUIDA', finalizada_em = SYSTIMESTAMP, mensagem_erro = :msg
             WHERE id = :id
            """,
            msg=mensagem[:4000], id=execucao_id,
        )
        cur.execute(
            """
            UPDATE arquivo_bruto SET status = 'AGUARDANDO_MAPEAMENTO', processado_em = SYSTIMESTAMP
             WHERE id = :id
            """,
            id=arquivo_id,
        )
    con.commit()


def falhar(con, arquivo_id: int, execucao_id: int | None, mensagem: str) -> None:
    """Registra a falha numa transação própria (a principal já foi desfeita)."""
    with con.cursor() as cur:
        if execucao_id is not None:
            cur.execute(
                """
                UPDATE execucao SET status = 'FALHOU', finalizada_em = SYSTIMESTAMP, mensagem_erro = :msg
                 WHERE id = :id
                """,
                msg=(mensagem or "")[:4000], id=execucao_id,
            )
        cur.execute(
            "UPDATE arquivo_bruto SET status = 'REJEITADO', processado_em = SYSTIMESTAMP WHERE id = :id",
            id=arquivo_id,
        )
    con.commit()
