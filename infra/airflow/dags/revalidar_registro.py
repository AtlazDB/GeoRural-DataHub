"""
### revalidar_registro

O "Revalidar" da quarentena. O back-end dispara esta DAG pela API REST do Airflow, passando
`{"quarentena_id": <id>}`, espera terminar (uns 3 s) e lê o resultado no XCom da tarefa:
`{status, erros, mensagem}`, com status APROVADO, REPROVADO ou RECUSADO. A regra é a mesma da carga
(`georural/regras.py`); ver `georural/revalidacao.py`.
"""

import pendulum
from airflow.sdk import Param, dag, get_current_context, task

from georural import config, revalidacao


@dag(
    dag_id="revalidar_registro",
    schedule=None,
    start_date=pendulum.datetime(2026, 10, 1, tz="America/Sao_Paulo"),
    catchup=False,
    # Ligada desde que aparece: é a tela que dispara, e cada operador revalida o seu registro.
    is_paused_upon_creation=False,
    max_active_runs=16,
    tags=["georural", "quarentena"],
    doc_md=__doc__,
    params={"quarentena_id": Param(None, type=["null", "integer"], description="id em registro_quarentena")},
)
def revalidar_registro():

    @task(retries=0)
    def revalidar() -> dict:
        params = get_current_context()["params"]
        if params.get("quarentena_id") is None:
            raise ValueError('Informe {"quarentena_id": <id>} na configuração do disparo')
        return revalidacao.revalidar(config.carregar(), int(params["quarentena_id"]))

    revalidar()


revalidar_registro()
