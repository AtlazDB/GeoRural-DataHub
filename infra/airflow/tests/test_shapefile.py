"""Extração do .zip e leitura em lotes, com um shapefile pequeno gerado no próprio teste."""

import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dags"))

from georural import shapefile  # noqa: E402


def _zip_com_shapefile(tmp_path: Path, quantidade: int, crs: str = "EPSG:4674") -> Path:
    gdf = gpd.GeoDataFrame(
        {"cod_imovel": [f"PR-1-{i}" for i in range(quantidade)], "num_area": [1.5] * quantidade},
        geometry=[Polygon([(i, 0), (i, 1), (i + 1, 1), (i + 1, 0)]) for i in range(quantidade)],
        crs=crs,
    )
    pasta = tmp_path / "origem"
    pasta.mkdir()
    gdf.to_file(pasta / "AREA_IMOVEL.shp")
    destino = tmp_path / "car.zip"
    with zipfile.ZipFile(destino, "w") as z:
        for arquivo in pasta.iterdir():
            z.write(arquivo, arcname=f"PR/{arquivo.name}")
    return destino


def test_extrai_e_le_em_lotes_em_4326(tmp_path):
    shps = shapefile.extrair(_zip_com_shapefile(tmp_path, 7), tmp_path / "extraido")
    assert [s.name for s in shps] == ["AREA_IMOVEL.shp"]

    lotes = list(shapefile.ler_em_lotes(shps[0], tamanho_lote=3))
    assert [primeira for primeira, _ in lotes] == [1, 4, 7]
    assert [len(lote) for _, lote in lotes] == [3, 3, 1]
    assert all(lote.crs.to_epsg() == 4326 for _, lote in lotes)


def test_le_em_lotes_sem_geometria(tmp_path):
    shp = shapefile.extrair(_zip_com_shapefile(tmp_path, 7), tmp_path / "extraido")[0]

    lotes = list(shapefile.ler_em_lotes(shp, tamanho_lote=3, geometria=False))

    assert [primeira for primeira, _ in lotes] == [1, 4, 7]
    assert all(list(lote.columns) == ["cod_imovel", "num_area"] for _, lote in lotes)
    assert shapefile.tem_geometria(shp)


def test_rejeita_zip_sem_dbf(tmp_path):
    origem = _zip_com_shapefile(tmp_path, 1)
    sem_dbf = tmp_path / "sem_dbf.zip"
    with zipfile.ZipFile(origem) as entrada, zipfile.ZipFile(sem_dbf, "w") as saida:
        for item in entrada.infolist():
            if not item.filename.lower().endswith(".dbf"):
                saida.writestr(item, entrada.read(item))
    with pytest.raises(shapefile.ArquivoInvalido, match="AREA_IMOVEL.dbf"):
        shapefile.extrair(sem_dbf, tmp_path / "extraido")


def test_rejeita_zip_slip(tmp_path):
    malicioso = tmp_path / "malicioso.zip"
    with zipfile.ZipFile(malicioso, "w") as z:
        z.writestr("../../fora.txt", "x")
    with pytest.raises(shapefile.ArquivoInvalido, match="fora da pasta"):
        shapefile.extrair(malicioso, tmp_path / "extraido")


def test_rejeita_zip_sem_shp(tmp_path):
    vazio = tmp_path / "vazio.zip"
    with zipfile.ZipFile(vazio, "w") as z:
        z.writestr("leia-me.txt", "nada")
    with pytest.raises(shapefile.ArquivoInvalido, match="Nenhum arquivo .shp"):
        shapefile.extrair(vazio, tmp_path / "extraido")


@pytest.mark.parametrize("conteudo,esperado", [("UTF-8", "UTF-8"), ("1252", "cp1252"), ("", "ISO-8859-1")])
def test_charset_pelo_cpg(tmp_path, conteudo, esperado):
    shp = tmp_path / "a.shp"
    shp.touch()
    (tmp_path / "a.cpg").write_text(conteudo)
    assert shapefile.charset(shp) == esperado


def test_charset_sem_cpg(tmp_path):
    shp = tmp_path / "a.shp"
    shp.touch()
    assert shapefile.charset(shp) == "ISO-8859-1"
