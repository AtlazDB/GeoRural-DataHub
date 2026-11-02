"""As regras do mapeamento de colunas, em Python: a validação e a conversão de cada valor (US11.T04).

Substitui o pkg_quality.validate (que validava a staging inteira no PL/SQL) e as conversões do
pkg_tratada.expressao. A carga (generico.py) e o "Revalidar" da quarentena chamam a mesma
validar(): o que reprova numa reprova na outra.

Entrada: uma tabela com o texto de cada coluna mapeada (como as regras liam do JSON da staging:
número com ponto, sem notação científica; ver leitura.texto) e a geometria já normalizada
(leitura.normalizar). Saída: os erros (um por linha e regra que falhou, na ordem em que o
pkg_quality os gravava), a chave natural e a geometria final (a corrigida, quando a política é
CORRIGIR_E_REGISTRAR e a correção sai válida).

Os códigos de erro e os textos dos motivos são os da V10 (tabela tipo_erro), os mesmos de antes.

Geometria: quem julga é o Shapely (GEOS, padrão OGC, sem tolerância), e não mais o Oracle (que
considera encostado o que fica a menos de 5 cm). Medido na Reserva Legal do IAT: ~5% dos polígonos
têm detalhes de centímetros (pontas finas, anéis quase encostados) que o Oracle reprova e o GEOS
aceita; esses agora entram como vieram, sem correção. O Oracle só guarda e filtra (SDO_FILTER,
SDO_RELATE); cálculo de área ou interseção de indicador deve ser feito aqui (GeoPandas), para o
motor que valida ser o mesmo que calcula. Os vértices repetidos a menos de 6 cm, que o Oracle
recusa até em consulta, já saem na leitura (leitura.wkb).
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import shapely
import shapely.errors
import shapely.ops
from pyproj import Geod, Transformer
from shapely.geometry.base import BaseGeometry

# Valores que a fonte manda no lugar de "vazio" (falso nulo).
FALSOS_NULOS = {"NULL", "N/A", "-"}
# Máscara da DATA quando a regra não traz formato_origem (a mesma do pkg_quality).
FORMATO_DATA = "DD/MM/YYYY"
# Quanto a área pode encolher numa correção automática de geometria (1%).
VARIACAO_AREA_CORRECAO = 0.01
# Margem da divisa para "fora do PR", em metros: o CAR e a malha do IAT discordam na divisa.
MARGEM_DIVISA_M = 1000
# Projeção em metros para medir a margem da divisa (SIRGAS 2000 / Brazil Polyconic).
CRS_METROS = 5880

TIPOS_GEOMETRIA = {"PONTO": 1, "LINHA": 2, "POLIGONO": 3}
_FAMILIA = {"Point": 1, "MultiPoint": 1, "LineString": 2, "MultiLineString": 2,
            "Polygon": 3, "MultiPolygon": 3, "GeometryCollection": 4}
_NOME_DO_TIPO = {"Point": "PONTO", "MultiPoint": "MULTIPONTO", "LineString": "LINHA",
                 "MultiLineString": "MULTILINHA", "Polygon": "POLIGONO", "MultiPolygon": "MULTIPOLIGONO",
                 "GeometryCollection": "COLECAO"}
_UF_DO_IBGE = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
               "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
               "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR",
               "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF"}
_GEOD = Geod(ellps="GRS80")


class RegraInvalida(Exception):
    """O mapeamento está errado (ex.: formato de geometria desconhecido). A carga inteira falha."""


@dataclass(frozen=True)
class Regra:
    """Uma linha da regra_campo, com o nome da coluna na fonte."""
    id: int
    coluna: str
    tabela: str
    campo: str
    tipo: str
    formato: str | None = None
    obrigatorio: bool = False
    chave: bool = False
    politica: str | None = None
    recortar_pr: bool = False
    dominio_id: int | None = None
    referencia: str | None = None
    cpf_cnpj: bool = False
    bloquear_regressao: bool = False
    ordem: int | None = None


@dataclass
class Contexto:
    """O que as regras consultam no banco, carregado uma vez por carga (ver carregar_contexto)."""
    dominios: dict[int, set[str]] = field(default_factory=dict)
    imoveis: set[str] = field(default_factory=set)
    municipios_ibge: set[str] = field(default_factory=set)
    municipios_pr: set[str] = field(default_factory=set)  # nomes sem acento e em minúsculas
    recorte_pr: BaseGeometry | None = None                 # o contorno do PR, em 4326
    # Regressão: valor gravado na carga anterior, por campo e chave natural (texto, como o Oracle concatena).
    anteriores: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class Validacao:
    erros: pd.DataFrame          # linha (posição), tipo_erro, campo, motivo, valor_recebido, ordem
    chave_natural: pd.Series     # texto ou None
    geometria: np.ndarray        # a geometria final de cada linha (a corrigida, quando houve)
    correcoes: pd.DataFrame      # posição, erro_original, geometria_original, geometria_corrigida

    def reprovadas(self) -> np.ndarray:
        return np.unique(self.erros["linha"].to_numpy()) if len(self.erros) else np.array([], dtype=int)


# ------------------------------------------------------------------ texto

def limpar(valor: str | None) -> str | None:
    """Texto sem espaços nas pontas, com vazio e falso nulo virando None (pkg_tratada.limpar)."""
    if valor is None:
        return None
    valor = valor.strip()
    return None if not valor or valor.upper() in FALSOS_NULOS else valor


def _aparado(serie: pd.Series) -> pd.Series:
    """TRIM do Oracle: tira os espaços das pontas, e texto vazio vira nulo."""
    aparada = serie.where(serie.isna(), serie.astype("object").map(lambda v: v.strip() if isinstance(v, str) else v))
    return aparada.where(aparada != "", None)


def _informado(serie: pd.Series) -> pd.Series:
    """Veio valor, sem ser falso nulo (o que as regras de tipo, CPF e referência conferem)."""
    return serie.notna() & ~serie.fillna("").str.upper().isin(FALSOS_NULOS)


# ------------------------------------------------------------------ datas

_TOKENS_DATA = [("YYYY", r"(?P<Y>\d{4})"), ("HH24", r"(?P<H>\d{2})"), ("MI", r"(?P<M>\d{2})"),
                ("SS", r"(?P<S>\d{2})"), ("MM", r"(?P<m>\d{2})"), ("DD", r"(?P<d>\d{2})"),
                ("YY", r"(?P<y>\d{2})")]


def _mascara(mascara: str) -> re.Pattern:
    """Máscara do Oracle com FX (exata) como expressão regular. Só os elementos que as fontes usam."""
    padrao, resto = "", mascara.strip().upper()
    while resto:
        for token, grupo in _TOKENS_DATA:
            if resto.startswith(token):
                padrao += grupo
                resto = resto[len(token):]
                break
        else:
            if resto[0].isalnum():
                raise RegraInvalida(f"Elemento de data não suportado na máscara '{mascara}'.")
            padrao += re.escape(resto[0])
            resto = resto[1:]
    return re.compile(f"^{padrao}$")


def _data(texto: str, mascaras: list[re.Pattern]) -> datetime | None:
    for padrao in mascaras:
        achado = padrao.match(texto)
        if not achado:
            continue
        g = achado.groupdict()
        ano = int(g["Y"]) if g.get("Y") else (2000 + int(g["y"]) if int(g["y"]) < 50 else 1900 + int(g["y"])) \
            if g.get("y") else datetime.now().year
        try:
            return datetime(ano, int(g.get("m") or datetime.now().month), int(g.get("d") or 1),
                            int(g.get("H") or 0), int(g.get("M") or 0), int(g.get("S") or 0))
        except ValueError:
            continue
    return None


def _mascaras(formato: str | None) -> list[re.Pattern]:
    return [_mascara(m) for m in (formato or FORMATO_DATA).split("|")]


def _epoch_ms(texto: str) -> datetime:
    return datetime(1970, 1, 1) + timedelta(milliseconds=int(texto))


# ------------------------------------------------------------------ CPF/CNPJ

def cpf_cnpj_valido(valor: str | None) -> bool:
    """Dígitos verificadores de CPF (11) ou CNPJ (14), com ou sem máscara (pkg_quality.valid_cpf_cnpj)."""
    if valor is None or not re.fullmatch(r"[0-9./ -]+", valor):
        return False
    digitos = re.sub(r"[^0-9]", "", valor)
    n = len(digitos)
    if n not in (11, 14) or digitos == digitos[0] * n:
        return False
    for d in (1, 2):
        conta = n - 3 + d
        soma = sum(int(digitos[i]) * ((conta + 1 - i) if n == 11 else ((conta - 1 - i) % 8 + 2))
                   for i in range(conta))
        verificador = 0 if soma % 11 < 2 else 11 - soma % 11
        if verificador != int(digitos[conta]):
            return False
    return True


def sem_acento(texto: str) -> str:
    """Comparação sem acento e sem maiúsculas (o NLS_SORT=BINARY_AI do Oracle)."""
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)).casefold()


# ------------------------------------------------------------------ geometria

def _valida(geometria) -> tuple[bool, str | None]:
    if geometria is None:
        return True, None
    razao = shapely.is_valid_reason(geometria)
    return razao == "Valid Geometry", razao


def corrigir(geometria: BaseGeometry, familia: int) -> BaseGeometry | None:
    """Correção automática: o make_valid, só com as partes do tipo esperado. Vale só se sair válida
    e, nos polígonos, sem perder mais de 1% da área (nem zerar). Era o pkg_quality.fix_geometry."""
    try:
        corrigida = shapely.make_valid(geometria, method="structure", keep_collapsed=False)
    except shapely.errors.GEOSException:
        return None
    if corrigida is None or corrigida.is_empty:
        return None
    if corrigida.geom_type == "GeometryCollection":
        partes = [g for g in shapely.get_parts(corrigida) if _FAMILIA.get(g.geom_type) == familia]
        if not partes:
            return None
        corrigida = shapely.union_all(partes)
    if _FAMILIA.get(corrigida.geom_type) != familia or not corrigida.is_valid:
        return None
    if familia == 3:
        antes, depois = area_m2(geometria), area_m2(corrigida)
        if depois <= 0 or (antes > 0 and depois < antes * (1 - VARIACAO_AREA_CORRECAO)):
            return None
        corrigida = shapely.orient_polygons(corrigida, exterior_cw=False)
    return corrigida


def area_m2(geometria: BaseGeometry) -> float:
    """Área geodésica, em m² (como o SDO_AREA no SRID 4326)."""
    try:
        return abs(_GEOD.geometry_area_perimeter(geometria)[0])
    except Exception:  # noqa: BLE001 - geometria inválida demais até para medir
        return 0.0


def fora_do_recorte(geometrias: np.ndarray, recorte: BaseGeometry, margem_m: float = MARGEM_DIVISA_M) -> np.ndarray:
    """True para cada geometria a mais de `margem_m` do recorte. Quem toca o recorte nem é medido;
    só as de fora vão para a projeção em metros."""
    shapely.prepare(recorte)
    fora = ~shapely.intersects(recorte, geometrias)
    if fora.any():
        para_metros = Transformer.from_crs(4326, CRS_METROS, always_xy=True).transform
        recorte_m = shapely.ops.transform(para_metros, recorte)
        medir = np.flatnonzero(fora)
        distantes = [shapely.ops.transform(para_metros, geometrias[i]).distance(recorte_m) > margem_m for i in medir]
        fora[medir] = distantes
    return fora


# ------------------------------------------------------------------ validação

def validar(textos: pd.DataFrame, geometrias: np.ndarray, regras: list[Regra], ctx: Contexto,
            camadas: pd.Series | None = None, linhas: pd.Series | None = None) -> Validacao:
    """Aplica as regras a todas as linhas de uma vez. `textos` tem uma coluna por coluna da fonte
    (texto ou None); `geometrias`, a geometria de cada linha (shapely ou None)."""
    n = len(textos)
    erros: list[tuple] = []  # (linha, tipo_erro, campo, motivo, valor_recebido)

    def acusar(mascara: np.ndarray | pd.Series, tipo_erro: str, campo: str, motivo, valores=None) -> None:
        posicoes = np.flatnonzero(np.asarray(mascara, dtype=bool))
        for p in posicoes:
            texto_motivo = motivo(p) if callable(motivo) else motivo
            valor = None if valores is None else valores.iloc[p]
            erros.append((int(p), tipo_erro, campo, texto_motivo,
                          None if valor is None or (isinstance(valor, float) and np.isnan(valor)) else str(valor)[:1000]))

    def coluna(nome: str) -> pd.Series:
        if nome in textos.columns:
            return textos[nome].astype("object").where(textos[nome].notna(), None).reset_index(drop=True)
        return pd.Series([None] * n, dtype="object")

    por_id = sorted(regras, key=lambda r: r.id)
    geo = next((r for r in por_id if r.tipo == "GEOMETRIA"), None)
    familia = None
    if geo is not None:
        familia = TIPOS_GEOMETRIA.get((geo.formato or "POLIGONO").strip().upper())
        if familia is None:
            raise RegraInvalida(f"formato_origem da geometria inválido: {geo.formato} (use POLIGONO, PONTO ou LINHA).")
        if geo.recortar_pr and ctx.recorte_pr is None:
            raise RegraInvalida("Nenhum município do PR na tabela municipio: carregue a malha do IBGE antes de validar.")

    # Obrigatório: ausente (não veio ou veio nulo) ou em branco (vazio, só espaços, falso nulo).
    for r in por_id:
        if not r.obrigatorio or r.tipo == "GEOMETRIA":
            continue
        valores = coluna(r.coluna)
        ausente = valores.isna().to_numpy()
        branco = (~ausente) & (_aparado(valores).isna() | valores.fillna("").str.strip().str.upper().isin(FALSOS_NULOS)).to_numpy()
        for p in np.flatnonzero(ausente | branco):
            if ausente[p]:
                erros.append((int(p), "CAMPO_OBRIGATORIO_AUSENTE", r.coluna, "Campo obrigatório não informado.", None))
            else:
                erros.append((int(p), "CAMPO_EM_BRANCO", r.coluna, "Campo obrigatório em branco ou com falso nulo.",
                              str(valores.iloc[p])[:1000]))

    # Tipo: valor informado que não converte. TEXTO aceita tudo; a geometria tem bloco próprio.
    for r in por_id:
        if r.tipo not in ("INTEIRO", "DECIMAL", "DATA", "BOOLEANO"):
            continue
        valores = _aparado(coluna(r.coluna))
        informado = _informado(valores)
        texto = valores.fillna("")
        if r.tipo == "INTEIRO":
            invalido = ~texto.str.fullmatch(r"[+-]?[0-9]+")
        elif r.tipo == "DECIMAL":
            separador = "[.,]" if (r.formato or "").upper() == "VIRGULA" else "[.]"
            invalido = ~texto.str.fullmatch(rf"[+-]?[0-9]*{separador}?[0-9]+")
        elif r.tipo == "BOOLEANO":
            invalido = ~texto.str.upper().isin({"SIM", "NÃO", "NAO"})
        elif (r.formato or "").upper() == "EPOCH_MS":
            invalido = ~texto.str.fullmatch(r"[0-9]{1,15}")
        else:
            mascaras = _mascaras(r.formato)
            invalido = texto.map(lambda v: _data(v, mascaras) is None)
        acusar(informado & invalido, "TIPO_DE_DADO_INVALIDO", r.coluna, f"Valor não converte para {r.tipo}.", valores)

    # Chave natural: as colunas de chave juntas com "|", na ordem da fonte. Nula se faltar parte.
    chaves = sorted((r for r in por_id if r.chave), key=lambda r: (r.ordem is None, r.ordem or 0, r.coluna))
    chave_natural = pd.Series([None] * n, dtype="object")
    if chaves:
        partes = [_aparado(coluna(r.coluna)) for r in chaves]
        completa = np.logical_and.reduce([p.notna().to_numpy() for p in partes])
        juntas = partes[0].fillna("")
        for p in partes[1:]:
            juntas = juntas + "|" + p.fillna("")
        chave_natural = juntas.str.slice(0, 400).where(completa, None)
        contagem = chave_natural.map(chave_natural.value_counts())
        nomes = ", ".join(r.coluna for r in chaves)[:128]
        acusar(chave_natural.notna() & (contagem > 1), "CHAVE_NATURAL_DUPLICADA", nomes,
               "Chave repetida em mais de uma linha do arquivo.", chave_natural)

    # Regressão: campo preenchido na carga anterior que chegou vazio (só com chave de uma coluna).
    if len(chaves) == 1:
        tabela_chave = chaves[0].tabela
        for r in por_id:
            if not r.bloquear_regressao or r.tabela != tabela_chave or r.tipo == "GEOMETRIA":
                continue
            anteriores = ctx.anteriores.get(r.campo, {})
            valores = _aparado(coluna(r.coluna))
            vazio = ~_informado(valores)
            anterior = chave_natural.map(lambda c: anteriores.get(c) if c is not None else None)
            acusar(chave_natural.notna() & vazio & anterior.notna(), "REGRESSAO_DE_VALOR", r.coluna,
                   lambda p: ("Campo preenchido na carga anterior chegou vazio. Valor anterior: " + anterior.iloc[p])[:400],
                   valores)

    # Geometria: nula, do tipo errado, inválida (corrigida ou não) e fora do PR.
    finais = np.array(geometrias, dtype=object, copy=True)
    correcoes: list[tuple] = []
    if geo is not None:
        presentes = np.array([g is not None and not g.is_empty for g in finais])
        if geo.obrigatorio:
            acusar(~presentes, "GEOMETRIA_NULA", geo.coluna, "Geometria obrigatória não informada.")
        do_tipo = np.array([presentes[i] and _FAMILIA.get(finais[i].geom_type) == familia for i in range(n)])
        esperado = next(k for k, v in TIPOS_GEOMETRIA.items() if v == familia)
        acusar(presentes & ~do_tipo, "GEOMETRIA_TIPO_INCOMPATIVEL", geo.coluna,
               lambda p: f"Geometria do tipo {_NOME_DO_TIPO.get(finais[p].geom_type, finais[p].geom_type)}; esperado {esperado}.")

        invalidas = np.zeros(n, dtype=bool)
        validas = shapely.is_valid(np.where(do_tipo, finais, None).astype(object))
        for p in np.flatnonzero(do_tipo & ~validas):
            razao = shapely.is_valid_reason(finais[p])
            corrigida = corrigir(finais[p], familia) if geo.politica == "CORRIGIR_E_REGISTRAR" else None
            if corrigida is not None:
                correcoes.append((int(p), razao[:400], finais[p], corrigida))
                finais[p] = corrigida
            else:
                invalidas[p] = True
                erros.append((int(p), "GEOMETRIA_INVALIDA", geo.coluna, f"Geometria inválida: {razao}"[:400], None))

        if geo.recortar_pr:
            conferir = np.flatnonzero(do_tipo & ~invalidas)
            if len(conferir):
                fora = fora_do_recorte(finais[conferir].astype(object), ctx.recorte_pr)
                for p in conferir[fora]:
                    erros.append((int(p), "FORA_DO_RECORTE_TERRITORIAL", geo.coluna,
                                  "Geometria a mais de 1 km do Paraná.", None))

    # Domínio: valor informado (mesmo falso nulo) fora da lista ativa do domínio.
    for r in por_id:
        if r.dominio_id is None:
            continue
        valores = _aparado(coluna(r.coluna))
        permitidos = ctx.dominios.get(r.dominio_id, set())
        acusar(valores.notna() & ~valores.isin(permitidos), "VALOR_FORA_DO_DOMINIO", r.coluna,
               "Valor fora da lista permitida.", valores)

    # CPF/CNPJ: dígitos verificadores.
    for r in por_id:
        if not r.cpf_cnpj:
            continue
        valores = _aparado(coluna(r.coluna))
        acusar(_informado(valores) & ~valores.map(cpf_cnpj_valido), "CPF_CNPJ_INVALIDO", r.coluna,
               "CPF ou CNPJ inválido.", valores)

    # Integridade cruzada: o valor precisa existir na tabela de referência.
    for r in por_id:
        if r.referencia is None:
            continue
        valores = _aparado(coluna(r.coluna))
        if r.referencia == "imovel_rural":
            existe = valores.isin(ctx.imoveis)
        else:
            existe = valores.map(lambda v: v is not None and (
                (bool(re.fullmatch(r"[0-9]{7}", v)) and v in ctx.municipios_ibge) or sem_acento(v) in ctx.municipios_pr))
        acusar(_informado(valores) & ~existe, "REFERENCIA_NAO_ENCONTRADA", r.coluna,
               f"Valor não encontrado em {r.referencia}.", valores)

    tabela_erros = pd.DataFrame(erros, columns=["linha", "tipo_erro", "campo", "motivo", "valor_recebido"])
    tabela_erros["ordem"] = range(len(tabela_erros))
    tabela_correcoes = pd.DataFrame(correcoes, columns=["linha", "erro_original", "geometria_original",
                                                        "geometria_corrigida"])
    return Validacao(tabela_erros, chave_natural, finais, tabela_correcoes)


def motivo_da_linha(erros: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por registro reprovado, como a quarentena guarda: o 1º erro dá o campo, o tipo e o
    valor recebido; o motivo junta todos com " | " (até 400 caracteres)."""
    ordenados = erros.sort_values(["linha", "ordem"])
    primeiro = ordenados.groupby("linha", sort=True).first()[["campo", "tipo_erro", "valor_recebido"]]
    motivo = ordenados.groupby("linha", sort=True)["motivo"].agg(lambda m: " | ".join(m)[:400])
    return primeiro.join(motivo).reset_index()


# ------------------------------------------------------------------ conversão para a Tratada

def converter(valor: str | None, regra: Regra):
    """O texto já no tipo do destino (pkg_tratada.expressao). Só para linhas aprovadas."""
    texto = limpar(valor)
    if texto is None:
        return None
    if regra.tipo == "INTEIRO":
        return int(texto)
    if regra.tipo == "DECIMAL":
        return float(texto.replace(",", ".") if (regra.formato or "").upper() == "VIRGULA" else texto)
    if regra.tipo == "DATA":
        if (regra.formato or "").upper() == "EPOCH_MS":
            return _epoch_ms(texto)
        return _data(texto, _mascaras(regra.formato))
    if regra.tipo == "BOOLEANO":
        return {"SIM": 1, "NÃO": 0, "NAO": 0}.get(texto.upper())
    # JSON_VALUE ... RETURNING VARCHAR2(4000): o que passa de 4000 bytes vira nulo
    return texto if len(texto.encode("utf-8")) <= 4000 else None


def campos_derivados(tabela: str, valores: dict) -> dict:
    """Os campos que a tabela exige e dá para deduzir (os mesmos do pkg_tratada.montar)."""
    extras = {}
    cod_imovel = valores.get("cod_imovel")
    if tabela == "imovel_rural" and "estado" not in valores and cod_imovel:
        extras["estado"] = cod_imovel[:2].upper() if len(cod_imovel) > 2 and cod_imovel[2] == "-" else None
    cod_ibge = valores.get("cod_ibge")
    if tabela == "municipio" and "estado" not in valores and cod_ibge:
        extras["estado"] = _UF_DO_IBGE.get(str(cod_ibge)[:2])
    return extras


# ------------------------------------------------------------------ banco

def carregar_regras(con, fonte_id: int) -> list[Regra]:
    with con.cursor() as cur:
        cur.execute("""
            SELECT rc.id, fc.nome_coluna, LOWER(rc.destino_tabela), LOWER(rc.campo_destino), rc.tipo_dado,
                   rc.formato_origem, rc.obrigatorio, rc.chave, rc.politica_geometria, rc.recortar_pr,
                   rc.dominio_id, rc.referencia_cruzada, rc.validar_cpf_cnpj, rc.bloquear_regressao, fc.ordem
              FROM regra_campo rc JOIN fonte_coluna fc ON fc.id = rc.fonte_coluna_id
             WHERE rc.fonte_id = :fonte ORDER BY rc.id""", fonte=fonte_id)
        return [Regra(id=int(r[0]), coluna=r[1], tabela=r[2], campo=r[3], tipo=r[4], formato=r[5],
                      obrigatorio=r[6] == 1, chave=r[7] == 1, politica=r[8], recortar_pr=r[9] == 1,
                      dominio_id=int(r[10]) if r[10] is not None else None, referencia=r[11],
                      cpf_cnpj=r[12] == 1, bloquear_regressao=r[13] == 1,
                      ordem=int(r[14]) if r[14] is not None else None) for r in cur]


def carregar_contexto(con, regras: list[Regra]) -> Contexto:
    """Só carrega o que alguma regra usa."""
    ctx = Contexto()
    with con.cursor() as cur:
        dominios = sorted({r.dominio_id for r in regras if r.dominio_id is not None})
        if dominios:
            cur.execute(f"SELECT dominio_id, valor FROM dominio_valor WHERE ativo = 1 "
                        f"AND dominio_id IN ({','.join(str(d) for d in dominios)})")
            for dominio, valor in cur:
                ctx.dominios.setdefault(int(dominio), set()).add(valor)
        referencias = {r.referencia for r in regras if r.referencia}
        if "imovel_rural" in referencias:
            cur.execute("SELECT cod_imovel FROM imovel_rural")
            ctx.imoveis = {v for (v,) in cur}
        if "municipio" in referencias:
            cur.execute("SELECT cod_ibge, nome, estado FROM municipio")
            for cod_ibge, nome, estado in cur:
                ctx.municipios_ibge.add(cod_ibge)
                if estado == "PR" and nome:
                    ctx.municipios_pr.add(sem_acento(nome))
        if any(r.recortar_pr for r in regras if r.tipo == "GEOMETRIA"):
            ctx.recorte_pr = contorno_pr(cur)
        chaves = [r for r in regras if r.chave]
        regressao = [r for r in regras if r.bloquear_regressao and r.tipo != "GEOMETRIA"
                     and len(chaves) == 1 and r.tabela == chaves[0].tabela]
        for r in regressao:
            tabela, chave = _nome_sql(chaves[0].tabela), _nome_sql(chaves[0].campo)
            campo = _nome_sql(r.campo)
            # "|| ''" converte como o Oracle concatenava no motivo (o NLS da sessão).
            cur.execute(f"SELECT {chave}, {campo} || '' FROM {tabela} WHERE {campo} IS NOT NULL")
            ctx.anteriores[r.campo] = {str(k): v for k, v in cur}
    return ctx


def contorno_pr(cur) -> BaseGeometry | None:
    """O contorno do PR: a união dos municípios do PR que estão na Tratada (None se não houver).
    Simplificado em ~20 m, como a grade do banco fazia: a margem da divisa é de 1 km."""
    cur.execute("SELECT SDO_UTIL.TO_WKBGEOMETRY(geometria) FROM municipio WHERE estado = 'PR'")
    municipios = [shapely.from_wkb(w.read() if hasattr(w, "read") else w) for (w,) in cur]
    if not municipios:
        return None
    return shapely.simplify(shapely.union_all(shapely.make_valid(np.array(municipios, dtype=object))), 2e-4)


def _nome_sql(nome: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,127}", nome):
        raise RegraInvalida(f"Nome de tabela ou coluna inválido no mapeamento: {nome}")
    return nome
