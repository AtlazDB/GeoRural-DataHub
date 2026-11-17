"""
### preencher_geometria_wkb

Preenche a `geometria_wkb` (V30 do back-end) das linhas de `imovel_rural` e `input_feature` que já
existiam antes dela. Roda **uma vez em cada ambiente**, depois de aplicar a V30. Sem ela o cálculo
dos indicadores continua certo, mas lento: a linha sem WKB é convertida pelo Oracle na hora.

Pode rodar de novo sem problema: só mexe em linha com geometria e sem WKB. A conversão é do Oracle,
em lotes com commit. A conta está em `georural/geometria_wkb.py`.
"""

import pendulum
from airflow.sdk import dag, task

from georural import config, geometria_wkb


@dag(
    dag_id="preencher_geometria_wkb",
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Sao_Paulo"),
    catchup=False,
    is_paused_upon_creation=False,
    max_active_runs=1,
    tags=["georural", "manutencao"],
    doc_md=__doc__,
)
def preencher_geometria_wkb():

    @task(retries=0)
    def preencher() -> dict:
        return geometria_wkb.fill(config.carregar())

    preencher()


preencher_geometria_wkb()
