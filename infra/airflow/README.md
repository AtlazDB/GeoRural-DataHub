# Ambiente do Airflow

Pipeline de ingestao do data lake. Roda numa VM Ampere A1 (**aarch64**) da OCI,
com Airflow 3.3.1 em `docker compose`, dentro de `~/airflow`.

## Arquivos

| Arquivo | O que e |
|---|---|
| `Dockerfile` | Imagem propria: Airflow 3.3.1 + bibliotecas do pipeline |
| `.env.example` | Modelo do `.env` da VM (Autonomous, bucket, chaves do Airflow). O `.env` real fica so na VM |
| `docker-compose.yaml` | Airflow de **producao** na VM: Postgres + api-server, scheduler e dag-processor (LocalExecutor). Tudo lido do `.env` |
| `docker-compose.local.yaml` | Airflow de **desenvolvimento** no seu PC (um container, `airflow standalone`) |
| `.env.local.example` | Modelo do `.env.local` do desenvolvimento |
| `dags/` | As DAGs (`teste_conexoes`, `coleta_fontes`, `coletar_fonte`, `varredor_bruta`, `processar_arquivo`) |
| `dags/georural/` | Codigo do pipeline usado pelas DAGs (testavel sem Airflow) |
| `tests/` | Testes do pipeline (pytest) |

## Rodar no seu computador (desenvolvimento)

Precisa do Docker Desktop. O Airflow roda num container e usa o **Oracle local** do
back-end e o **bucket real** da OCI (com a sua chave da API, a mesma do back-end).

1. Suba o Oracle local: `cd Atlaz4-BackEnd && docker compose up -d`
2. Rode o back-end uma vez (`mvnw spring-boot:run`) para o Flyway criar as tabelas
3. Aqui em `infra/airflow`: `cp .env.local.example .env.local` e preencha
4. Suba o Airflow:
   ```bash
   docker compose -f docker-compose.local.yaml --env-file .env.local up --build
   ```
5. Abra http://localhost:8088 (sem login em dev; a 8080 fica para o back-end) e rode a DAG `teste_conexoes`:
   as duas tarefas verdes = Oracle e bucket funcionando
6. Ative as DAGs `varredor_bruta`, `processar_arquivo`, `coleta_fontes` e `coletar_fonte`. O back-end
   não processa nada: todo upload, todo clique em "Processar" e todo "Executar" de fonte por link
   fica com o Airflow.
7. Para a demonstração com as fontes por link (municípios, focos, Reserva Legal e CAR pelo WFS),
   rode uma vez o `db/seed-demo/fontes.sql` do back-end: ele cadastra as 4 fontes com o mapeamento.

Testes do pipeline (dentro da imagem, que ja tem as bibliotecas):
```bash
docker compose -f docker-compose.local.yaml --env-file .env.local run --rm \
  -v ./tests:/opt/airflow/tests airflow bash -c "pip install -q pytest && pytest -q /opt/airflow/tests"
```

### Como a coleta por link funciona

```
"Executar" na tela ──> fonte.coleta_solicitada_em ──> coleta_fontes (a cada 2 min)
                                                         │ reserva o pedido
                                                         ▼
                                                   coletar_fonte (uma por vez)
                                                     pergunta o total, pagina o servico
                                                     (ArcGIS ou WFS) em ordem fixa, varias
                                                     paginas ao mesmo tempo, confere
                                                     coletado = total, zip de paginas GeoJSON
                                                         │ mesmo hash da ultima? "sem mudanca"
                                                         ▼
                                     bucket bruta/<SIGLA>/... + arquivo_bruto RECEBIDO (COLETA)
```

* `georural/coleta.py`. Pagina ArcGIS por `resultOffset`/`resultRecordCount` ordenado pelo
  objectid, e WFS por `startIndex`/`count` ordenado pela coluna-chave do mapeamento.
* Paginas em paralelo: a 1a vai sozinha (diz quantos registros o servidor entrega por pedido); as
  demais viram faixas fixas, ate 4 ao mesmo tempo (`GEORURAL_COLETA_PARALELO` troca; 1 = uma por
  vez). O zip e gravado na ordem das faixas: sai igual com 1 ou com N pedidos, e o hash do "sem
  mudanca" continua valendo. Medido: no WFS do SICAR cada pagina de 5.000 imoveis leva ~21 s com 1
  pedido, 13 s com 2 e 10 s com 4 (o CAR do PR cai de ~40 para ~19 min); no recorte de 20 mil
  poligonos da Reserva Legal do IAT, 30 s com 1 pedido e 16 s com 4.
* Faltou registro ou veio repetido entre paginas: a coleta **falha** ("vieram X de Y") em vez de
  gravar dado pela metade. HTTP 500 ou demora na pagina: ela e dividida pela metade (ate 50). Pagina
  vazia no meio da fonte (o ArcGIS do IAT faz isso sob carga, com HTTP 200) e pedida de novo. Se
  uma faixa falhar ou vier incompleta com pedidos em paralelo, a coleta segue **uma pagina por vez**
  daquela faixa em diante (a mensagem da fonte avisa) e so falha se nem assim o servidor entregar.
* O log da coleta e a mensagem da fonte trazem a duracao; o resumo do processamento traz os
  segundos de cada etapa (`tempos`: leitura, contexto, validacao, gravacao, conclusao, indicadores)
  e o que a carga fez na Tratada (`inseridos`, `alterados`, `iguais`, `removidos`).
* O andamento e o resultado aparecem na tela da fonte (`fonte.ultima_coleta_mensagem`).

### Como o processamento funciona

```
upload/coleta ──> arquivo_bruto (RECEBIDO) ──> varredor_bruta (a cada 2 min)
                                                  │ reserva: RECEBIDO → PROCESSANDO
                                                  ▼
                                            processar_arquivo
                                              preparar:  abre a execucao
                                              processar: CAR (.zip da fonte CAR) ou generico
                                                  │
                                                  ▼
                       arquivo_bruto PROCESSADO | REJEITADO | AGUARDANDO_MAPEAMENTO
```

* **Caminho do CAR** (`car.py`): o shapefile de imoveis da fonte CAR, com as regras em Python.
* **Caminho generico** (`generico.py`): todo o resto, guiado pelo mapeamento da fonte. Registra
  as colunas que vieram (`fonte_coluna`; sem mapeamento o arquivo fica AGUARDANDO_MAPEAMENTO),
  aplica o desempate de codigo repetido quando o mapeamento tem `situacao`, valida com as regras
  do mapeamento (`regras.py`) e grava a quarentena e a Tratada (`tratada.py`), com commit a cada
  bloco. No fim recalcula IRL/IFC quando o conjunto e deles e algo mudou.
* **So o que mudou e gravado.** Cada linha tem um hash (`hash_registro`: sha1 dos valores mapeados
  mais a geometria). Com chave, entra a chave nova e muda a de hash diferente; sem chave, carga-retrato
  por imovel (`cod_imovel`) ou pelo conjunto da fonte, saindo so o que nao veio mais. A linha igual
  nao e tocada e fica com a versao da carga que a gravou. Se a carga falhar no meio, os blocos ja
  gravados ficam, e a proxima so refaz o que falta.
* **Geometria:** quem julga e o Shapely (GEOS, padrao OGC, sem tolerancia). O Oracle considerava
  encostado o que fica a menos de 5 cm e reprovava ~5% dos poligonos da Reserva Legal por detalhes de
  centimetros; esses entram como vieram. O Oracle so guarda e filtra (`SDO_FILTER`, `SDO_RELATE`):
  indicador que calcule area ou intersecao deve ser feito aqui, em GeoPandas, para o motor que valida
  ser o mesmo que calcula.
* Le em lotes de `GEORURAL_TAMANHO_LOTE` registros: a memoria depende do lote,
  nao do tamanho do arquivo.
* Medido na Reserva Legal do IAT (20 mil poligonos): 164 s com a staging e o PL/SQL de antes; 58 s
  na 1a carga e 29 s na carga sem mudanca.
* Na VM troque `GEORURAL_OCI_AUTH=instance_principal` e aponte `GEORURAL_DB_*` para o Oracle
  da VM; o codigo e o mesmo.

### Como o "Revalidar" da quarentena funciona

```
tela ──> back-end ──> API REST do Airflow ──> DAG revalidar_registro (~3 s) ──> resposta na tela
                      dispara, acompanha e        as mesmas regras da carga; se passar, grava
                      le o resultado no XCom      na Tratada e encerra o registro
```

* Mesmas regras e mesma gravacao da carga (`georural/revalidacao.py`). Trava o registro, recusa o ja
  encerrado e nao deixa um registro antigo voltar o dado de uma carga mais nova.
* **O back-end precisa alcancar a API do Airflow:** `AIRFLOW_URL`, `AIRFLOW_USUARIO` e `AIRFLOW_SENHA`
  no `.env` dele. Com o Airflow fora do ar, o Revalidar responde 503 com a mensagem; o resto da aplicacao segue.
* A DAG `revalidar_registro` nasce ativa (`is_paused_upon_creation=False`).

## Subir na VM (producao)

O Airflow da VM usa o **Autonomous Database** da OCI (`georural-dev`) e o bucket real. Nada de
Oracle local. A UI **nao fica exposta na internet**: escuta so em `127.0.0.1` da VM.

**1. Na OCI (uma vez)**
* *Bucket*: a VM entra no dynamic group `airflow-dg` (regra do compartimento raiz) e uma policy da
  acesso aos objetos do bucket (Instance Principal, sem chave em disco).
* *Banco*: em *Autonomous Database > georural-dev > Network*, coloque o IP publico da VM na
  **Access control list** e desligue o **mTLS obrigatorio**. Em *Database connection > TLS*, copie a
  string do `georuraldev_tp` para o `GEORURAL_DB_DSN`.
* *Usuario do Airflow* (opcional, recomendado): `georural_airflow`, so com DML nas tabelas e
  EXECUTE nos pacotes. Toda migration que criar tabela precisa do GRANT para ele.

**2. Na VM**
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER        # saia e entre de novo no SSH
git clone https://github.com/AtlazDB/Atlaz4.git ~/Atlaz4 && cd ~/Atlaz4/infra/airflow
cp .env.example .env && nano .env    # cada variavel diz de onde tirar o valor
mkdir -p logs config
docker compose up -d --build
```

**3. Conferir**
```bash
ssh -L 8088:localhost:8088 ubuntu@<IP da VM>      # do seu computador
cat ~/Atlaz4/infra/airflow/config/simple_auth_manager_passwords.json.generated   # senha do admin
```
Abra http://localhost:8088, rode a DAG `teste_conexoes` (as duas tarefas verdes = Autonomous e
bucket funcionando) e ative `varredor_bruta`, `processar_arquivo`, `coleta_fontes` e `coletar_fonte`.
O back-end so marca os arquivos e os pedidos de coleta no banco, e o Airflow os pega. As duas VMs nao precisam se enxergar.

## Como aplicar uma mudanca

Sempre de dentro de `~/Atlaz4/infra/airflow` na VM (antes era `~/airflow`, na VM antiga). Fora dela o compose responde
`no configuration file provided: not found`, e o erro vem enterrado embaixo de
varios avisos de `FERNET_KEY` — da pra achar que o comando funcionou.

```bash
cd ~/Atlaz4/infra/airflow
git pull
docker compose up -d --build   # ~1 min de indisponibilidade
```

Conferir que as bibliotecas subiram:

```bash
cd ~/Atlaz4/infra/airflow && docker compose exec airflow-scheduler \
  python -c "import geopandas,shapely,pyproj,pyogrio,requests,oci;print('ok',geopandas.__version__)"
```

## Armadilhas conhecidas

- **`fiona` nao instala em ARM.** Sem wheel para Linux ARM em nenhuma versao (a VM antiga era
  Ampere; a atual e AMD E2.2). Use `pyogrio`, que ja vem junto do geopandas 1.x.
- **A imagem tem nome proprio** (`atlaz4/airflow:producao`): o build nao sobrescreve a tag oficial
  `apache/airflow:3.3.1`, que na VM antiga perdia as bibliotecas a cada `docker pull`.
- **Autonomous Database Always Free: 20 GB.** As 4 fontes do PR inteiro ocupam ~8 GB em repouso,
  sem a staging, nao ha mais pico durante a carga. O banco tambem para sozinho depois de 7 dias
  sem uso: religue antes de uma demonstracao.
- **A autenticacao com a OCI e por Instance Principal**, nao por chave em disco:
  dynamic group + policy na tenancy. Isso nao se recria copiando arquivo, e
  configuracao na OCI.

## Pendencias

- [x] `docker-compose.yaml` versionado, sem senha no arquivo (tudo do `.env`).
- [x] `FERNET_KEY` obrigatoria: o compose nao sobe sem ela.
- [x] UI fora da internet (so `127.0.0.1`, acesso por tunel SSH) e senha gerada na primeira subida.
- [ ] Deploy automatico na VM do Airflow, no mesmo padrao do back (`deploy.sh` + chave SSH presa a ele).
- [ ] Desligar a VM antiga do Airflow, se ainda estiver no ar com a UI publica.
