"""
### coletar_fonte

Coleta o link de uma fonte inteiro e grava o resultado na zona bruta, como um upload.

* serviço ArcGIS ou WFS: página por página, conferindo o total; vira um zip de páginas GeoJSON
* arquivo (zip, CSV, GeoJSON): baixado de uma vez

No fim, se o conteúdo for igual ao da última coleta (mesmo hash), nada é gravado ("sem mudança").
Senão, o zip vai para `bruta/<SIGLA>/<data>/...`, nasce um `arquivo_bruto` (origem COLETA) e esta
DAG dispara o `processar_arquivo`. O andamento e o resultado aparecem na tela da fonte
(`fonte.ultima_coleta_mensagem`).

**Como é disparada**
* pelo "Executar" da tela: o back-end chama a API REST do Airflow com `{"fonte_id": 7, "pedido": true}`
* à mão, pela interface do Airflow (*Trigger DAG w/ config*): `{"fonte_id": 7}`

Uma coleta por vez (`max_active_runs=1`): os serviços públicos de origem agradecem.
"""

import tempfile
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sdk import Param, dag, get_current_context, task

from georural import bucket, coleta, config, fontes, gravacao
from georural.banco import conectar


def _registrar_falha(context) -> None:
    """Chamado só quando a tarefa falha de vez: a tela da fonte mostra o motivo."""
    erro = context.get("exception") or "falha sem mensagem (veja o log da tarefa no Airflow)"
    with conectar(config.carregar()) as con:
        fontes.registrar_fim(con, int(context["params"]["fonte_id"]), f"A coleta falhou: {erro}")


@dag(
    dag_id="coletar_fonte",
    schedule=None,
    start_date=pendulum.datetime(2026, 10, 1, tz="America/Sao_Paulo"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,  # sem agendamento: só roda quando um botão dispara
    tags=["georural", "coleta"],
    doc_md=__doc__,
    params={
        "fonte_id": Param(None, type=["null", "integer"], description="id em fonte"),
        "pedido": Param(False, type="boolean", description="true quando veio do Executar da tela"),
    },
)
def coletar_fonte():

    @task(retries=0, execution_timeout=timedelta(hours=6), on_failure_callback=_registrar_falha)
    def coletar() -> dict:
        params = get_current_context()["params"]
        if params.get("fonte_id") is None:
            raise ValueError('Informe {"fonte_id": <id>} na configuração do disparo')
        fonte_id = int(params["fonte_id"])
        cfg = config.carregar()

        with conectar(cfg) as con, tempfile.TemporaryDirectory(prefix="coleta-") as pasta:
            # Dois cliques no Executar disparam duas execuções: a segunda não acha mais o pedido.
            if params.get("pedido") and not fontes.reservar(con, fonte_id):
                return {"resultado": "JA_ATENDIDO"}
            fonte = fontes.buscar(con, fonte_id)
            resultado = coleta.coletar(
                fonte.url, Path(pasta), fonte.sigla,
                chaves=fontes.colunas_chave(con, fonte_id),
                metodo=fonte.metodo, cabecalhos=fonte.cabecalhos, corpo=fonte.corpo,
                progresso=lambda mensagem: fontes.registrar_andamento(con, fonte_id, mensagem),
            )

            anterior = fontes.ultimo_hash(con, fonte_id)
            if anterior and anterior[1] == resultado.hash_sha256:
                fontes.registrar_fim(con, fonte_id, f"Sem mudança desde a última coleta (arquivo {anterior[0]}): "
                                                    f"{resultado.mensagem()}")
                return {"resultado": "SEM_MUDANCA", "arquivo_bruto_id": anterior[0]}

            fontes.registrar_andamento(con, fonte_id, f"{fontes.PREFIXO_ANDAMENTO}: enviando para a zona bruta.")
            chave = fontes.chave_na_zona_bruta(cfg.oci_zona_bruta, fonte.sigla, resultado.nome)
            bucket.enviar(cfg, chave, resultado.caminho, resultado.content_type)
            arquivo_id = fontes.gravar_arquivo(con, fonte_id, chave, resultado.nome, resultado.tamanho,
                                               resultado.content_type, resultado.hash_sha256)
            fontes.registrar_fim(con, fonte_id, f"{resultado.mensagem()} Arquivo {arquivo_id} na fila do processamento.")
            return {"resultado": "RECEBIDO", "arquivo_bruto_id": arquivo_id, "total": resultado.total,
                    "coletados": resultado.coletados, "paginas": resultado.paginas, "bytes": resultado.tamanho}

    @task
    def reservar_arquivo(resultado: dict) -> list[dict]:
        """Coleta que gravou arquivo novo: reserva e manda processar (sem arquivo, a tarefa de disparo é pulada)."""
        if resultado["resultado"] != "RECEBIDO":
            return []
        arquivo_id = resultado["arquivo_bruto_id"]
        with conectar(config.carregar()) as con:
            if not gravacao.buscar_arquivo(con, arquivo_id).processavel or not gravacao.reservar(con, arquivo_id):
                return []
        agora = pendulum.now("UTC").format("YYYYMMDDHHmmss")
        return [{"trigger_run_id": f"arquivo-{arquivo_id}-{agora}",
                 "conf": {"arquivo_bruto_id": arquivo_id, "reservado": True}}]

    TriggerDagRunOperator.partial(
        task_id="disparar_processamento",
        trigger_dag_id="processar_arquivo",
        wait_for_completion=False,
    ).expand_kwargs(reservar_arquivo(coletar()))


coletar_fonte()
