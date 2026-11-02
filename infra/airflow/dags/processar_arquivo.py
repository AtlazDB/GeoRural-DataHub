"""
### processar_arquivo

Processa um arquivo da zona bruta, por um de dois caminhos:

* **CAR** (`car.py`): o shapefile de imóveis da fonte CAR (.zip), com as regras em Python.
* **genérico** (`generico.py`): todo o resto (fontes por link, GeoJSON), guiado pelo mapeamento da
  fonte (regra_campo): regras em Python (`regras.py`), Tratada só com o que mudou (`tratada.py`) e quarentena.
  Sem mapeamento, o arquivo fica AGUARDANDO_MAPEAMENTO, com as colunas registradas.

**Como é disparada**
* pelo back-end (upload e "Processar", pela API REST) ou pelo `coletar_fonte`, que já reservam o arquivo
  (status PROCESSANDO): `{"arquivo_bruto_id": 42, "reservado": true}`
* à mão, pela interface do Airflow (*Trigger DAG w/ config*): `{"arquivo_bruto_id": 42}` — serve para reprocessar

Uma execução por vez (`max_active_runs=1`): dois arquivos gravando ao mesmo tempo disputariam
as mesmas linhas no MERGE.
"""

from dataclasses import asdict
from datetime import timedelta

import pendulum
from airflow.sdk import Param, dag, get_current_context, task

from georural import car, config, generico, gravacao
from georural.banco import conectar


def _registrar_falha(context) -> None:
    """Chamado só quando a tarefa falha de vez (depois das novas tentativas)."""
    params = context["params"]
    execucao_id = context["ti"].xcom_pull(task_ids="preparar")
    erro = context.get("exception") or "falha sem mensagem (veja o log da tarefa no Airflow)"
    car.registrar_falha(config.carregar(), int(params["arquivo_bruto_id"]), execucao_id, erro)


@dag(
    dag_id="processar_arquivo",
    schedule=None,
    start_date=pendulum.datetime(2026, 10, 1, tz="America/Sao_Paulo"),
    catchup=False,
    max_active_runs=1,
    tags=["georural", "ingestao"],
    doc_md=__doc__,
    params={
        "arquivo_bruto_id": Param(None, type=["null", "integer"], description="id em arquivo_bruto"),
        "reservado": Param(False, type="boolean", description="true quando quem disparou já reservou"),
    },
)
def processar_arquivo():

    @task(retries=0)
    def preparar() -> int:
        """Confere o arquivo, garante a reserva e abre a execução. Devolve o id da execução."""
        contexto = get_current_context()
        params = contexto["params"]
        if params.get("arquivo_bruto_id") is None:
            raise ValueError('Informe {"arquivo_bruto_id": <id>} na configuração do disparo')

        arquivo_id = int(params["arquivo_bruto_id"])
        reservado = bool(params.get("reservado"))
        cfg = config.carregar()
        try:
            return car.preparar(cfg, arquivo_id, reservado, contexto["dag_run"].run_id)
        except Exception as erro:
            # Se o arquivo já veio reservado e a preparação falhou, devolve como REJEITADO
            # para ele não ficar preso em PROCESSANDO. Num disparo manual não mexe em nada
            # (o erro pode ser justamente "já está sendo processado").
            if reservado:
                car.registrar_falha(cfg, arquivo_id, None, erro)
            raise

    @task(
        retries=1,
        retry_delay=timedelta(minutes=2),
        execution_timeout=timedelta(hours=8),  # o CAR inteiro pelo caminho genérico passa de 3 h
        on_failure_callback=_registrar_falha,
    )
    def processar(execucao_id: int) -> dict:
        params = get_current_context()["params"]
        arquivo_id = int(params["arquivo_bruto_id"])
        cfg = config.carregar()
        with conectar(cfg) as con:
            caminho_car = gravacao.buscar_arquivo(con, arquivo_id).caminho_car
        modulo = car if caminho_car else generico
        return asdict(modulo.processar(cfg, arquivo_id, execucao_id))

    processar(preparar())


processar_arquivo()
