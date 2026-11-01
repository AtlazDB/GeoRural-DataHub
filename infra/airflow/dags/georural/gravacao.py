"""Toda a escrita do processamento no Oracle, em SQL direto — o mesmo SQL do GravadorImoveis
do back-end. Usa as tabelas como estão hoje (Flyway V1 a V7).

A gravação dos aprovados ainda é o MERGE daqui; a quarentena já sai completa (tabela de
destino, chave, tipo de erro, versão e geometria), que é o que o "Revalidar" da tela precisa
para o pkg_tratada.gravar_registro gravar o registro depois.
"""

from dataclasses import dataclass

import oracledb
import shapely

from georural import sdo
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
# ("DAG processar_arquivo") é o que o pkg_tratada.substituir_pendentes procura para
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
           status_analise, justificativa, analisado_por, analisado_em)
    VALUES (:execucao_id, :numero_linha, :campo, :motivo, :payload,
            :entidade, :chave_natural, :tipo_erro, :valor_recebido, :dataset_versao_id, :geometria,
            :status_analise, :justificativa, :analisado_por,
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


def gravar_quarentena(cur, execucao_id: int, dataset_versao_id: int, reprovados: list[Resultado]) -> None:
    if not reprovados:
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
                "status_analise": "DESCARTADO" if r.descartado_automaticamente else "PENDENTE",
                "justificativa": r.justificativa if r.descartado_automaticamente else None,
                "analisado_por": AUTOR_DESCARTE_AUTOMATICO if r.descartado_automaticamente else None,
                "descartado": 1 if r.descartado_automaticamente else 0,
            }
            for r in reprovados
        ],
    )


def geometria_quarentena(tipos: sdo.TiposSdo, geometria):
    """A geometria como chegou, para o visualizador e o "Revalidar". Só polígonos (os outros
    tipos o sdo.py não converte: ficam sem geometria). Vai no sentido que o Oracle exige, para
    um registro reprovado por outro motivo (código duplicado) poder ser gravado depois."""
    if geometria is None or geometria.is_empty or geometria.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    return tipos.converter(shapely.orient_polygons(geometria, exterior_cw=False), sdo.SRID_BANCO)


# ------------------------------------------------------------------ fim

def concluir(cur, arquivo_id: int, execucao_id: int, versao: Versao,
             lidos: int, validos: int, invalidos: int) -> int:
    """Fecha a carga. Devolve quantos registros antigos da quarentena ela substituiu
    (pkg_tratada.substituir_pendentes: reprocessar não duplica a fila)."""
    cur.execute(
        """
        UPDATE execucao SET status = 'CONCLUIDA', finalizada_em = SYSTIMESTAMP,
               registros_lidos = :lidos, registros_validos = :validos, registros_invalidos = :invalidos
         WHERE id = :id
        """,
        lidos=lidos, validos=validos, invalidos=invalidos, id=execucao_id,
    )
    cur.execute("UPDATE dataset_versao SET registros = :n WHERE id = :id", n=validos, id=versao.id)
    substituidos = int(cur.callfunc("pkg_tratada.substituir_pendentes", oracledb.DB_TYPE_NUMBER, [execucao_id]))
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
