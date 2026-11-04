"""
### calcular_indicadores

Recalcula o IRL e o IFC de todos os imóveis e grava em `indicador_imovel` (`georural/irl_ifc.py`,
no lugar do `pkg_indicadores`). A carga (`processar_arquivo`) já recalcula sozinha quando o CAR, a
Reserva Legal ou os focos mudam; esta DAG é para o "Calcular agora" da tela e para recalcular
depois de uma falha.

Configuração do disparo (as duas opcionais):
- `indicadores`: `["IRL"]`, `["IFC"]` ou os dois (padrão);
- `referencia`: fim da janela do IFC em UTC, `"2026-11-04T12:00:00"` (padrão: agora).
"""

from datetime import datetime

import pendulum
from airflow.sdk import Param, dag, get_current_context, task

from georural import config, irl_ifc


@dag(
    dag_id="calcular_indicadores",
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 1, tz="America/Sao_Paulo"),
    catchup=False,
    # Ligada desde que aparece: é a tela que dispara.
    is_paused_upon_creation=False,
    # Um cálculo por vez: dois MERGE no mesmo indicador_imovel disputariam as mesmas linhas.
    max_active_runs=1,
    tags=["georural", "indicadores"],
    doc_md=__doc__,
    params={
        "indicadores": Param(["IRL", "IFC"], type="array", description="IRL, IFC ou os dois"),
        "referencia": Param(None, type=["null", "string"], description="fim da janela do IFC, UTC (ISO)"),
    },
)
def calcular_indicadores():

    @task(retries=0)
    def calcular() -> dict:
        params = get_current_context()["params"]
        indicadores = tuple(params.get("indicadores") or ("IRL", "IFC"))
        desconhecidos = set(indicadores) - {"IRL", "IFC"}
        if desconhecidos:
            raise ValueError(f"Indicadores desconhecidos: {sorted(desconhecidos)} (use IRL e/ou IFC)")
        referencia = datetime.fromisoformat(params["referencia"]) if params.get("referencia") else None
        return irl_ifc.calculate(config.carregar(), indicadores, referencia=referencia)

    calcular()


calcular_indicadores()
