"""
### calcular_indicador

Calcula um indicador cadastrado pelo usuário (US13) para todos os imóveis e grava em
`indicator_result`. O back-end dispara esta DAG pela API REST do Airflow
(`POST /api/v1/indicators/{code}/calculate`), passando `{"code": "<sigla>"}`, e não espera: o
resultado aparece na consulta dos resultados. A conta está em `georural/custom_indicators.py`.
"""

import pendulum
from airflow.sdk import Param, dag, get_current_context, task

from georural import config, custom_indicators


@dag(
    dag_id="calcular_indicador",
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Sao_Paulo"),
    catchup=False,
    # Ligada desde que aparece: é a tela que dispara.
    is_paused_upon_creation=False,
    # Um cálculo por vez: dois MERGE no mesmo indicator_result disputariam as mesmas linhas.
    max_active_runs=1,
    tags=["georural", "indicadores"],
    doc_md=__doc__,
    params={"code": Param(None, type=["null", "string"], description="sigla em indicator_definition")},
)
def calcular_indicador():

    @task(retries=0)
    def calcular() -> dict:
        params = get_current_context()["params"]
        if not params.get("code"):
            raise ValueError('Informe {"code": "<sigla>"} na configuração do disparo')
        return custom_indicators.calculate(config.carregar(), params["code"])

    calcular()


calcular_indicador()
