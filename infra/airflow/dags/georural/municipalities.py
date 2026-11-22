"""Município de cada imóvel (imovel_rural.municipio_id).

Nenhuma etapa da carga preenchia o municipio_id: a regra de referência cruzada do CAR só
valida o nome. O código do imóvel no CAR já traz o IBGE do município (PR-4113700-...,
posições 4 a 10), o mesmo atalho que o IRL usa (irl_ifc.py). Só mexe nos que estão sem
município; o gatilho de histórico (V25) ignora, porque a versão do imóvel não muda.
"""

# Conjuntos (dataset_versao.conjunto) cuja carga pede resolver o município de novo:
# imóveis novos chegam sem ele, e uma malha municipal nova cobre códigos que faltavam.
CONJUNTOS = {"imoveis_rurais", "municipios"}

SQL_RESOLVE = """
    UPDATE imovel_rural i
       SET municipio_id = (SELECT m.id FROM municipio m
                            WHERE m.cod_ibge = SUBSTR(i.cod_imovel, 4, 7))
     WHERE i.municipio_id IS NULL
       AND REGEXP_LIKE(i.cod_imovel, '^[A-Z]{2}-[0-9]{7}-')
       AND EXISTS (SELECT 1 FROM municipio m WHERE m.cod_ibge = SUBSTR(i.cod_imovel, 4, 7))
"""


def resolve_municipalities(con) -> int:
    """Preenche o município dos imóveis que estão sem ele. Devolve quantos foram preenchidos."""
    with con.cursor() as cur:
        cur.execute(SQL_RESOLVE)
        total = cur.rowcount
    con.commit()
    return total
