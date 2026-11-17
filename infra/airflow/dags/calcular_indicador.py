"""
### calcular_indicador

Calcula um indicador cadastrado pelo usuário (US13) para todos os imóveis e grava em
`indicator_result`. O back-end dispara esta DAG pela API REST do Airflow
(`POST /api/v1/indicators/{code}/calculate`), passando `{"code": "<sigla>", "calculation_id": <id>}`,
e não espera: o resultado aparece na consulta dos resultados. A conta está em
`georural/custom_indicators.py`.

`calculation_id` é a linha da `indicator_calculation` (US13.T18) que o back criou: a DAG grava ali o
andamento (EM_ANDAMENTO, % e etapa) e o fim (CONCLUIDO ou ERRO), para a barra da tela. Sem ele
(disparo manual pelo Airflow), calcula do mesmo jeito, só sem barra.
"""

import pendulum
from airflow.sdk import Param, dag, get_current_context, task

from georural import config, custom_indicators


def _marcar_erro(context) -> None:
    """A tarefa falhou sem passar pelo except (ex.: foi derrubada pelo Airflow ou ficou sem memória):
    não deixa o cálculo parado em EM_ANDAMENTO na tela. Se o except já gravou o erro, não muda nada."""
    calculation_id = (context.get("params") or {}).get("calculation_id")
    if calculation_id:
        custom_indicators.finish_calculation(
            config.carregar(), int(calculation_id), "O cálculo foi interrompido no Airflow. Tente de novo.")


@dag(
    dag_id="calcular_indicador",
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Sao_Paulo"),
    catchup=False,
    # Ligada desde que aparece: é a tela que dispara.
    is_paused_upon_creation=False,
    # Um cálculo por vez: dois MERGE no mesmo indicator_result disputariam as mesmas linhas.
    # Os outros disparos esperam na fila (NA_FILA na tela).
    max_active_runs=1,
    tags=["georural", "indicadores"],
    doc_md=__doc__,
    params={
        "code": Param(None, type=["null", "string"], description="sigla em indicator_definition"),
        "calculation_id": Param(None, type=["null", "integer"],
                                description="linha da indicator_calculation (o back cria); vazio = sem barra"),
    },
)
def calcular_indicador():

    @task(retries=0, on_failure_callback=_marcar_erro)
    def calcular() -> dict:
        context = get_current_context()
        params = context["params"]
        if not params.get("code"):
            raise ValueError('Informe {"code": "<sigla>"} na configuração do disparo')
        cfg = config.carregar()
        calculation_id = int(params["calculation_id"]) if params.get("calculation_id") else None
        if calculation_id:
            custom_indicators.start_calculation(cfg, calculation_id, context.get("run_id"))
        try:
            resumo = custom_indicators.calculate(
                cfg, params["code"], custom_indicators.CalculationProgress(cfg, calculation_id))
        except Exception as erro:
            if calculation_id:
                custom_indicators.finish_calculation(cfg, calculation_id, str(erro) or type(erro).__name__)
            raise
        if calculation_id:
            custom_indicators.finish_calculation(cfg, calculation_id)
        return resumo

    calcular()


calcular_indicador()
