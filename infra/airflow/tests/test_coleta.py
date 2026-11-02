"""Coleta paginada contra serviços falsos: sem rede, sem banco."""

import hashlib
import json
import threading
import time
import zipfile
from urllib.parse import parse_qsl, urlsplit

import pytest

from georural import coleta

ARCGIS = "https://geo.exemplo.gov.br/server/rest/services/focos/FeatureServer/0/query?where=1=1&outFields=*&f=geojson&outSR=4674"
WFS = ("https://geoserver.exemplo.gov.br/geoserver/sicar/wfs?service=WFS&version=2.0.0&request=GetFeature"
       "&typeNames=sicar:imoveis&outputFormat=application/json&count=10000")


def _feicao(i: int, prefixo: str = "") -> dict:
    return {"type": "Feature", "id": f"{prefixo}{i}", "properties": {"objectid": i},
            "geometry": {"type": "Point", "coordinates": [-51.0, -25.0]}}


class ServidorFalso(coleta.ClienteHttp):
    """Responde como um ArcGIS ou um GeoServer com `total` registros."""

    def __init__(self, total: int, limite: int = 2000, falha_acima: int | None = None,
                 repete_pagina: bool = False, prefixo: str = ""):
        self.total = total
        self.limite = limite
        self.falha_acima = falha_acima
        self.repete_pagina = repete_pagina
        self.prefixo = prefixo
        self.pedidos: list[dict] = []

    def texto(self, url: str) -> bytes:
        p = {k.lower(): v for k, v in parse_qsl(urlsplit(url).query)}
        self.pedidos.append(p)
        if url.split("?")[0].endswith("/FeatureServer/0"):
            return json.dumps({"maxRecordCount": self.limite, "objectIdField": "OBJECTID",
                               "advancedQueryCapabilities": {"supportsPagination": True}}).encode()
        if p.get("returncountonly") == "true":
            return json.dumps({"count": self.total}).encode()
        if p.get("resulttype") == "hits":
            return f'<wfs:FeatureCollection numberMatched="{self.total}" numberReturned="0"/>'.encode()

        inicio = int(p.get("resultoffset", p.get("startindex", 0)))
        pedido = int(p.get("resultrecordcount", p.get("count", 0)))
        if self.falha_acima and pedido > self.falha_acima:
            raise coleta._ErroDoServidor("HTTP 500")
        if self.repete_pagina and inicio > 0:
            inicio -= 1  # ordem instável: devolve um registro da página anterior
        fim = min(self.total, inicio + min(pedido, self.limite))
        return json.dumps({"type": "FeatureCollection",
                           "features": [_feicao(i, self.prefixo) for i in range(inicio, fim)]}).encode()


def _ids(caminho) -> list:
    with zipfile.ZipFile(caminho) as z:
        partes = sorted(n for n in z.namelist() if n.startswith("parte-"))
        return [f["id"] for n in partes for f in json.loads(z.read(n))["features"]]


def test_tipo_pela_url():
    assert coleta.tipo(ARCGIS) == coleta.TIPO_ARCGIS
    assert coleta.tipo(WFS) == coleta.TIPO_WFS
    assert coleta.tipo("https://x.gov.br/dados/focos.csv") == coleta.TIPO_ARQUIVO


def test_arcgis_coleta_tudo_ordenado_pelo_objectid(tmp_path):
    servidor = ServidorFalso(total=4500)

    r = coleta.coletar(ARCGIS, tmp_path, "FOCOS-IAT", cliente=servidor, pausa=0)

    assert (r.total, r.coletados, r.paginas) == (4500, 4500, 3)
    assert _ids(r.caminho) == [str(i) for i in range(4500)]
    paginas = sorted((p for p in servidor.pedidos if "resultoffset" in p), key=lambda p: int(p["resultoffset"]))
    assert [p["resultoffset"] for p in paginas] == ["0", "2000", "4000"]
    assert all(p["orderbyfields"] == "OBJECTID" and p["f"] == "geojson" and p["outsr"] == "4674" for p in paginas)


def test_wfs_remove_a_paginacao_do_link_e_ordena_pela_chave(tmp_path):
    servidor = ServidorFalso(total=12000, limite=10000, prefixo="imoveis.")

    r = coleta.coletar(WFS, tmp_path, "CAR-WFS", chaves=["cod_imovel"], cliente=servidor, pausa=0)

    assert r.coletados == 12000
    paginas = sorted((p for p in servidor.pedidos if "startindex" in p), key=lambda p: int(p["startindex"]))
    assert [p["startindex"] for p in paginas] == ["0", "5000", "10000"]
    assert [p["count"] for p in paginas] == ["5000", "5000", "2000"]  # a última pede só o que falta
    assert all(p["sortby"] == "cod_imovel" for p in paginas)
    hits = next(p for p in servidor.pedidos if p.get("resulttype") == "hits")
    assert "outputformat" not in hits  # o total vem no XML


def test_servidor_que_entrega_menos_que_o_pedido(tmp_path):
    servidor = ServidorFalso(total=2500, limite=1000, prefixo="x.")

    r = coleta.coletar(WFS, tmp_path, "W", cliente=servidor, pausa=0)

    assert (r.coletados, r.paginas) == (2500, 3)


def test_pagina_com_erro_500_e_dividida(tmp_path):
    servidor = ServidorFalso(total=3000, falha_acima=600)

    r = coleta.coletar(ARCGIS, tmp_path, "F", cliente=servidor, pausa=0)

    assert r.coletados == 3000
    tamanhos = [int(p["resultrecordcount"]) for p in servidor.pedidos if "resultrecordcount" in p]
    assert tamanhos[:3] == [2000, 1000, 500]


def test_falta_registro_falha_com_vieram_x_de_y(tmp_path):
    class Encolhe(ServidorFalso):
        """Diz que tem 5.000, mas só entrega 4.000 (e continua dizendo 5.000)."""
        def texto(self, url):
            if "resultOffset=4000" in url:
                return json.dumps({"type": "FeatureCollection", "features": []}).encode()
            return super().texto(url)

    with pytest.raises(coleta.ColetaFalhou, match="Vieram 4.000 de 5.000"):
        coleta.coletar(ARCGIS, tmp_path, "F", cliente=Encolhe(total=5000), pausa=0)


def test_registro_repetido_entre_paginas_falha(tmp_path):
    with pytest.raises(coleta.ColetaFalhou, match="repetidos"):
        coleta.coletar(ARCGIS, tmp_path, "F", cliente=ServidorFalso(total=4000, repete_pagina=True), pausa=0)


def test_zip_deterministico(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = coleta.coletar(ARCGIS, tmp_path / "a", "F", cliente=ServidorFalso(total=2100), pausa=0)
    b = coleta.coletar(ARCGIS, tmp_path / "b", "F", cliente=ServidorFalso(total=2100), pausa=0)

    assert a.hash_sha256 == b.hash_sha256
    assert a.hash_sha256 == hashlib.sha256(a.caminho.read_bytes()).hexdigest()
    with zipfile.ZipFile(a.caminho) as z:
        assert all(i.compress_type == zipfile.ZIP_DEFLATED for i in z.infolist())
        assert json.loads(z.read("coleta.json"))["total"] == 2100


def test_validar_url_recusa_rede_interna():
    for url in ("http://127.0.0.1/x", "http://169.254.169.254/latest", "http://10.0.0.5/a", "ftp://x.gov.br/a"):
        with pytest.raises(coleta.ColetaFalhou):
            coleta.validar_url(url)


# ------------------------------------------------------------------ páginas em paralelo

class ServidorLento(ServidorFalso):
    """Demora para responder e conta quantos pedidos de página estão abertos ao mesmo tempo. As
    páginas do começo demoram mais: as respostas chegam fora de ordem."""

    def __init__(self, total: int, **kwargs):
        super().__init__(total, **kwargs)
        self.trava = threading.Lock()
        self.abertos = 0
        self.maximo = 0

    def texto(self, url: str) -> bytes:
        inicio = dict(parse_qsl(urlsplit(url).query)).get("resultOffset")
        if inicio is None:
            return super().texto(url)
        with self.trava:
            self.abertos += 1
            self.maximo = max(self.maximo, self.abertos)
        try:
            time.sleep(0.05 if int(inicio) < 6000 else 0.01)
            return super().texto(url)
        finally:
            with self.trava:
                self.abertos -= 1


def test_zip_igual_com_um_e_com_quatro_pedidos_ao_mesmo_tempo(tmp_path):
    (tmp_path / "1").mkdir()
    (tmp_path / "4").mkdir()
    um = coleta.coletar(ARCGIS, tmp_path / "1", "F", cliente=ServidorLento(total=9500), pausa=0, paralelo=1)
    lento = ServidorLento(total=9500)
    quatro = coleta.coletar(ARCGIS, tmp_path / "4", "F", cliente=lento, pausa=0, paralelo=4)

    assert um.hash_sha256 == quatro.hash_sha256  # o "sem mudança" continua valendo
    assert _ids(quatro.caminho) == [str(i) for i in range(9500)]  # na ordem, mesmo chegando fora dela
    assert lento.maximo == 4


def test_um_pedido_por_vez_quando_paralelo_e_1(tmp_path):
    lento = ServidorLento(total=9500)

    coleta.coletar(ARCGIS, tmp_path, "F", cliente=lento, pausa=0, paralelo=1)

    assert lento.maximo == 1


def test_so_a_faixa_com_erro_e_dividida(tmp_path):
    class FalhaEm4000(ServidorFalso):
        def texto(self, url):
            p = dict(parse_qsl(urlsplit(url).query))
            if p.get("resultOffset") == "4000" and int(p["resultRecordCount"]) > 500:
                raise coleta._ErroDoServidor("HTTP 500")
            return super().texto(url)

    servidor = FalhaEm4000(total=8000)
    r = coleta.coletar(ARCGIS, tmp_path, "F", cliente=servidor, pausa=0, paralelo=3)

    assert _ids(r.caminho) == [str(i) for i in range(8000)]
    pedidos = {(p["resultoffset"], p["resultrecordcount"]) for p in servidor.pedidos if "resultoffset" in p}
    assert {("2000", "2000"), ("6000", "2000")} <= pedidos  # as outras faixas seguem com 2.000
    # 4000+2000 falhou, 4000+1000 também: 4000+500 e 4500+500 resolvem, e 5000+1000 já passa
    assert {("4000", "500"), ("4500", "500"), ("5000", "1000")} <= pedidos


def test_servidor_que_entrega_menos_no_meio_tem_o_resto_pedido_de_novo(tmp_path):
    class CortaEm2000(ServidorFalso):
        def texto(self, url):
            p = dict(parse_qsl(urlsplit(url).query))
            if p.get("resultOffset") == "2000":
                url = url.replace("resultRecordCount=2000", "resultRecordCount=1500")
            return super().texto(url)

    r = coleta.coletar(ARCGIS, tmp_path, "F", cliente=CortaEm2000(total=6000), pausa=0, paralelo=2)

    assert _ids(r.caminho) == [str(i) for i in range(6000)]
    assert r.paginas == 4  # 0-2000, 2000-3500, 3500-4000, 4000-6000


def test_paralelo_pela_variavel_de_ambiente(monkeypatch):
    monkeypatch.delenv("GEORURAL_COLETA_PARALELO", raising=False)
    assert coleta._paralelo() == coleta.PARALELO
    monkeypatch.setenv("GEORURAL_COLETA_PARALELO", "1")
    assert coleta._paralelo() == 1


def test_mensagem_traz_a_duracao(tmp_path):
    r = coleta.coletar(ARCGIS, tmp_path, "F", cliente=ServidorFalso(total=2100), pausa=0)

    assert r.segundos is not None
    assert r.mensagem().startswith("2.100 registros coletados em 2 página(s) em ")


def test_pagina_vazia_no_meio_e_pedida_de_novo(tmp_path):
    class VaziaUmaVez(ServidorFalso):
        """Como o ArcGIS do IAT sob carga: HTTP 200 sem feição, e no pedido seguinte vem certo."""
        def __init__(self, total):
            super().__init__(total)
            self.vazias = {"4000", "6000"}

        def texto(self, url):
            inicio = dict(parse_qsl(urlsplit(url).query)).get("resultOffset")
            if inicio in self.vazias:
                self.vazias.discard(inicio)
                return json.dumps({"type": "FeatureCollection", "features": []}).encode()
            return super().texto(url)

    r = coleta.coletar(ARCGIS, tmp_path, "F", cliente=VaziaUmaVez(total=8000), pausa=0, paralelo=4)

    assert _ids(r.caminho) == [str(i) for i in range(8000)]
    assert r.paginas == 4
