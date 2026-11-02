# Manual de usuário — GeoRural DataHub

**Equipe Atlaz** · Projeto Fatec para a Visiona — Tecnologia Espacial
São José dos Campos - SP · 2º semestre de 2026

Integrantes: Gabriel Rocha · Leandro Campos · Manuela Brito Migri · Gabriel Valente ·
Gabriel Nunes · Rodolfo Corbalan


## Sumário

1. [Agradecimentos](#agradecimentos)
2. [Apresentação](#apresentação)
3. [Requisitos de sistema](#requisitos-de-sistema)
   - [Funcionais](#funcionais)
   - [Não funcionais](#não-funcionais)
4. [Como instalar e rodar](#como-instalar-e-rodar)
   - [Back-end](#back-end)
   - [Front-end](#front-end)
   - [Primeira carga de dados](#primeira-carga-de-dados)
5. [Como usar](#como-usar)
   - [Visão geral da tela](#visão-geral-da-tela)
   - [Fontes de dados](#fontes-de-dados)
   - [Mapeamento de colunas](#mapeamento-de-colunas)
   - [Quarentena](#quarentena)
   - [Imóveis rurais](#imóveis-rurais)
   - [Rastreabilidade](#rastreabilidade)
6. [Mensagens comuns e o que fazer](#mensagens-comuns-e-o-que-fazer)
7. [Limitações conhecidas](#limitações-conhecidas)


## Agradecimentos

"Agradecemos à Visiona — Tecnologia Espacial pela oportunidade de desenvolver este sistema web.
Como grupo, nos comprometemos a simplificar o seu dia a dia e otimizar a sua rotina de trabalho."

**Gabriel Rocha** — Agradeço à Visiona pela oportunidade de desenvolver este projeto e,
principalmente, à minha equipe pela parceria e pelo trabalho conjunto durante todo o processo.

**Leandro Campos** — _a escrever_

**Manuela Brito Migri** — Agradeço aos meus colegas de API pelo apoio e companheirismo ao longo
deste trabalho. Grandes conquistas não são feitas por mentes isoladas, mas por equipes que
compartilham a mesma visão com respeito e cumplicidade.

**Gabriel Valente** — _a escrever_

**Gabriel Nunes** — Grato aos companheiros da Visiona por colaborarem com o progresso dos nossos
estudos, e aos meus colegas de equipe por somarem na produtividade e atenderem ao problema da nossa
empresa parceira.

**Rodolfo Corbalan** — _a escrever_


## Apresentação

A Visiona atua em projetos que usam informações territoriais, ambientais e geoespaciais para apoiar
análise, planejamento e tomada de decisão. Nesses projetos, os dados sobre imóveis rurais vêm de
várias fontes, públicas e privadas, e incluem registros cadastrais e informações geográficas
produzidas ao longo do tempo. Como essas fontes têm formatos variados, níveis de qualidade
diferentes e são atualizadas com frequência, fica difícil rastrear e auditar os dados. Muitas vezes
não se consegue saber de qual fonte, versão e processamento veio uma informação exibida, e isso
prejudica a transparência e a confiabilidade.

Para resolver esse problema, o projeto propõe o **GeoRural DataHub**, uma plataforma web com
back-end em **Spring Boot (Java)** e front-end em **Vue.js**, sobre um banco **Oracle** com dados
espaciais. A plataforma:

- cataloga as **fontes de dados** e guarda cada arquivo recebido sem alterações, com prova de
  integridade (hash SHA-256);
- **processa e valida** os dados do Cadastro Ambiental Rural (CAR), da Reserva Legal do CAR e dos
  focos de calor do INPE antes de disponibilizá-los;
- retém numa **quarentena** os registros que não passam nas regras, para que sejam corrigidos,
  revalidados ou descartados com justificativa;
- permite definir, por fonte, **para onde vai cada coluna** e quais regras de qualidade valem para
  ela;
- exibe os **imóveis rurais do Paraná** em mapa e tabela, com os **indicadores** de Reserva Legal
  (IRL) e de focos de calor (IFC) de cada imóvel;
- mostra a **rastreabilidade** de cada indicador: de qual arquivo, versão e regra veio o número.


## Requisitos de sistema

### Funcionais

#### RF01 — Cadastro de fontes e importação de arquivos

> Como Operador de Dados, quero cadastrar fontes de dados e importar seus arquivos para que os
> dados recebidos sejam armazenados de forma íntegra e possam ser usados pela aplicação.

- O sistema permite cadastrar uma fonte informando nome, sigla (até 12 caracteres, gravada em
  maiúsculas), órgão responsável, formato (CSV, GeoJSON, SHP, XLSX ou JSON) e periodicidade de
  atualização (diária, semanal, mensal, anual ou eventual).
- O sistema impede duas fontes com a mesma sigla, sem diferenciar maiúsculas de minúsculas.
- O sistema lista as fontes cadastradas, com o status de cada uma (**Ativa**, **Sem arquivo** ou
  **Inativa**) e o último arquivo recebido.
- O sistema permite importar um arquivo para uma fonte nos formatos `.csv`, `.zip`, `.geojson`,
  `.json`, `.shp`, `.xlsx` ou `.txt`, com até 500 MB.
- O arquivo original é guardado sem alterações na **zona bruta** do Object Storage, com o hash
  SHA-256 e a data/hora do recebimento. Essa cópia nunca é sobrescrita.
- O sistema informa o status de cada arquivo: **Recebido**, **Processando**, **Processado**,
  **Rejeitado** ou **Aguardando mapeamento**.
- O sistema permite excluir um arquivo **Rejeitado** que nunca gerou versão de dados. Arquivos
  processados não podem ser excluídos, para preservar o histórico.
- O sistema permite cadastrar uma fonte que é coletada por **link** (serviço WFS/GeoServer, ArcGIS
  ou arquivo para download), informando o método (GET ou POST), cabeçalhos opcionais e, no POST, o
  corpo da requisição; testar a conexão com o link antes de salvar; e coletá-la sob demanda pelo
  botão **Executar**. O arquivo coletado é guardado na zona bruta como um envio comum. Se o
  conteúdo baixado for idêntico ao da última coleta, nada é gravado. Por segurança, links que
  apontam para endereços internos da rede são recusados.

#### RF02 — Processamento e validação dos dados

> Como Operador de Dados, quero que os arquivos sejam processados e validados antes de ficarem
> disponíveis, para que só dados confiáveis cheguem à consulta.

- O sistema processa arquivos de qualquer fonte, guiado pelo mapeamento de colunas: `.zip` com
  shapefile (`.shp`, `.shx`, `.dbf`, `.prj`) ou com as páginas GeoJSON da coleta por link, e
  `.geojson`/`.json`.
- O processamento começa sozinho (pelo Airflow) assim que o arquivo chega, sem outra ação do
  Operador.
- Cada registro é validado. No CAR, por exemplo: código vazio ou repetido, geometria ausente, que
  não seja polígono, inválida ou totalmente fora do Paraná. Os reprovados vão para a
  **quarentena**, com o tipo de erro e o motivo; os demais são gravados.
- Os focos de calor de outros estados são apenas contados e ignorados; não vão para a quarentena.
- O sistema ajusta o sentido do contorno dos polígonos para o padrão exigido pelo banco.
- Reprocessar um arquivo não duplica dados: imóveis são atualizados pelo código do CAR, e focos já
  existentes não são inseridos de novo.
- Ao final, o arquivo fica **Processado** (com o total de registros lidos, válidos e inválidos) ou
  **Rejeitado** (com a mensagem de erro). Se ocorrer erro, nada daquele arquivo é gravado.
- O sistema permite processar de novo um arquivo **Recebido**, **Rejeitado** ou **Aguardando
  mapeamento**.
- Cada imóvel gravado mantém o vínculo com a versão do conjunto de dados e com a execução que o
  produziu.

#### RF03 — Mapeamento de colunas e regras de qualidade

> Como Operador de Dados, quero definir para onde vai cada coluna de uma fonte e quais regras
> valem para ela, para que a validação siga as regras do negócio.

- O sistema mostra as colunas detectadas nos arquivos da fonte, marcadas como **Mapeada**,
  **Nova** (chegou e ainda não tem destino) ou **Ausente** (não veio no último arquivo).
- A fonte avisa na lista quando tem colunas novas sem mapeamento.
- Para cada coluna, o Operador define a tabela e o campo de destino, o tipo de dado (texto,
  inteiro, decimal, data, booleano ou geometria) e as regras: obrigatório, chave do registro,
  bloqueio de regressão, domínio de valores, referência cruzada (município ou imóvel), validação de
  CPF/CNPJ, formato de data e, para geometria, a política para geometria inválida (quarentena ou
  correção automática) e a conferência de que está no Paraná.
- O sistema permite remover um mapeamento.

#### RF04 — Quarentena

> Como Operador de Dados, quero analisar e tratar os registros reprovados, para corrigir o que
> for possível sem alterar o dado original.

- O sistema lista os registros retidos, com filtros por texto, tipo de erro, fonte e status, e
  mostra métricas por status (pendentes, em análise, corrigidos, revalidados, descartados).
- O registro passa pelos status **Pendente → Em análise → Corrigido → Revalidado**, ou termina como
  **Descartado** (com justificativa obrigatória) ou **Substituído** (quando uma carga mais nova
  trouxe o mesmo registro).
- O dado original nunca é alterado: a correção é guardada à parte.
- O Operador pode corrigir os campos do registro e, quando o problema é a geometria, editar os
  vértices no mapa, desenhar uma geometria nova ou pedir uma correção automática, que é mostrada
  como prévia antes de ser salva.
- Ao **revalidar**, o registro passa de novo por todas as regras. Se aprovado, é gravado na zona
  tratada; se reprovado, o sistema lista os erros que continuam.
- Registros revalidados, descartados ou substituídos ficam encerrados, com o histórico guardado
  para auditoria.

#### RF05 — Consulta de imóveis rurais via API

> Como Analista, quero consultar os imóveis rurais por meio de uma API para que suas informações
> territoriais possam ser consumidas pela aplicação.

- O sistema disponibiliza uma API que lista os imóveis de forma paginada (até 500 por página),
  sem a geometria, com código, município, área (ha) e situação no CAR (Ativo, Pendente, Cancelado
  ou Suspenso).
- O sistema permite filtrar por município (nome), situação no CAR e parte do código do imóvel.
- O sistema permite consultar um imóvel pelo código do CAR, com a geometria em GeoJSON.
- O sistema devolve os imóveis de uma área do mapa em GeoJSON, com geometria simplificada e um
  limite de quantidade.

#### RF06 — Visualização em mapa e tabela

> Como Analista, quero visualizar os imóveis rurais e as divisões territoriais em um mapa para
> localizar e analisar espacialmente os imóveis do Paraná.

- O sistema exibe os imóveis do Paraná em um mapa interativo, sincronizado com uma tabela.
- Abaixo de um nível de zoom, o mapa não carrega imóveis e pede ao usuário que aproxime.
- O mapa fica restrito ao Paraná e desenha o contorno do município escolhido no filtro.
- O mapa exibe os focos de calor do INPE considerados pelo indicador IFC.
- Ao selecionar um imóvel no mapa ou na tabela, a seleção é sincronizada e o sistema mostra
  código, município, área e situação.

#### RF07 — Indicadores e rastreabilidade

> Como Analista, quero ver os indicadores ambientais de cada imóvel e saber de onde veio cada
> número, para confiar no resultado e poder auditá-lo.

- O sistema calcula, para cada imóvel:
  - **IRL — Índice de Reserva Legal:** área de Reserva Legal ÷ área do imóvel. O mínimo é 20%
    (Mata Atlântica e Cerrado): a partir dele o imóvel fica **Conforme**; abaixo, **Crítico**. O
    sistema mostra quantos hectares faltam ou sobram.
  - **IFC — Índice de Focos de Calor:** focos dentro do imóvel a cada 1.000 ha, nos últimos 12
    meses, do satélite de referência (AQUA_M-T). Sem focos: **Sem focos**; abaixo de 20:
    **Alerta**; 20 ou mais: **Crítico**.
- O cálculo é disparado sob demanda e vale para todos os imóveis de uma vez.
- As regras de cálculo são versionadas; recalcular não duplica resultados.
- Para cada indicador, o sistema mostra o caminho até a origem: fonte, arquivo, hash e versão dos
  dados de entrada; a regra e a fórmula aplicadas; e a conta do resultado.
- O sistema mostra o histórico de cálculos de um indicador e o que mudou em relação ao cálculo
  anterior (o dado, a regra ou os dois).

### Não funcionais

**Tecnologia e arquitetura**
- Back-end em Java 17 com Spring Boot; front-end em Vue 3 com Vite, Leaflet e Tailwind.
- Banco de dados Oracle; dados geoespaciais em colunas `SDO_GEOMETRY`, acessadas via Hibernate
  Spatial e JTS. As regras de qualidade, a gravação na zona tratada e o cálculo dos indicadores
  rodam em pacotes PL/SQL no próprio banco.
- Estrutura do banco versionada com Flyway (criada automaticamente na primeira subida).
- API REST em JSON; geometrias em GeoJSON.
- Arquivos originais guardados na zona bruta do Object Storage da Oracle Cloud (OCI).
- O processamento e a coleta são feitos pelo orquestrador **Apache Airflow** (configurado em
  `infra/airflow` neste repositório). O back-end só registra os pedidos e o Airflow faz o trabalho:
  processa qualquer fonte pelo mapeamento de colunas, coleta os links em páginas e recalcula os
  indicadores ao final.

**Integridade e rastreabilidade dos dados**
- O processamento de um arquivo é transacional: se ocorrer erro, nenhum dado parcial é gravado.
- O arquivo original e o registro original na quarentena nunca são alterados.
- Cada imóvel e cada indicador mantêm o vínculo com a versão dos dados e a execução que os
  produziram.
- O histórico de arquivos processados, execuções, quarentena e cálculos não é apagado.
- As geometrias são armazenadas em **EPSG:4326 (WGS84)**. As fontes originais (CAR, IBGE) usam
  SIRGAS 2000; a diferença entre os dois é menor que 1 metro, abaixo da precisão do próprio CAR, e
  por isso é ignorada de propósito — decisão registrada na documentação técnica.

**Desempenho**
- As listagens são sempre paginadas e não trazem geometria.
- O mapa carrega só a área visível, a partir de um nível de zoom, com no máximo 1.000 imóveis por
  vez (a API aceita até 3.000) e avisa quando há mais imóveis do que o exibido. As geometrias são
  simplificadas conforme o zoom.
- O limite de tamanho de arquivo para importação é de 500 MB.

**Segurança**
- A coleta por link só aceita `http`/`https`, recusa endereços internos da rede, limita
  redirecionamentos, tempo de resposta e tamanho do download.
- Cabeçalhos de requisição das fontes por link não são devolvidos pela API.

**Usabilidade e confiabilidade**
- As mensagens de erro da API vêm em português, com o código HTTP apropriado.
- O andamento do processamento é atualizado automaticamente na tela, sem recarregar a página.
- A interface tem modo claro e modo escuro.

**Manutenibilidade**
- Código versionado no GitHub, com o fluxo de branches `feature → dev → main` e revisão por Pull
  Request.
- O back-end segue arquitetura em camadas (`controller` → `service` → `repository`).
- A memória de cálculo dos indicadores, com exemplos reais e o passo a passo de conferência no
  QGIS, está em `docs/memoria-de-calculo/` no repositório do back-end.


## Como instalar e rodar

O GeoRural é formado por dois projetos: o **back-end** (a API) e o **front-end** (a tela). Os dois
precisam estar rodando para usar a aplicação por completo.

### Back-end

**Pré-requisitos:** JDK 17, Docker Desktop, Git e uma credencial válida da Oracle Cloud (OCI) para
o Object Storage.

1. Clone o repositório `Atlaz4-BackEnd`.
2. Copie o `.env.example` para `.env`, na raiz do projeto, e preencha `OCI_NAMESPACE`,
   `OCI_BUCKET`, `DB_USERNAME` e `DB_PASSWORD`. Para processar e coletar, suba também o Airflow
   (`infra/airflow/README.md` deste repositório).
3. Configure a credencial da OCI em `~/.oci/config`, no perfil `DEFAULT`. **Sem esse arquivo a
   aplicação não inicia**, mesmo que você não vá enviar arquivos.
4. Suba o banco: `docker compose up -d`. A primeira subida demora alguns minutos; o banco está
   pronto quando `docker logs oracle` mostrar `DATABASE IS READY TO USE!`.
5. Rode a aplicação: `mvnw.cmd spring-boot:run` (Windows) ou `./mvnw spring-boot:run`. As tabelas
   são criadas automaticamente na primeira subida.
6. A API fica disponível em `http://localhost:8080/api/v1`.

### Front-end

**Pré-requisitos:** Node.js 18 ou mais recente e npm 9 ou mais recente.

1. Clone o repositório `Atlaz4-FrontEnd`.
2. Instale as dependências: `npm install`.
3. Copie o `.env.example` para `.env.development`.
4. Edite o `.env.development` e defina `VITE_API_URL=http://localhost:8080/api/v1`, com o back-end
   já rodando.
5. Rode `npm run dev`. O navegador abre sozinho em `http://localhost:5173`.

> **Só para testar o envio de arquivos, sem o back-end:** mantenha o `VITE_API_URL` do
> `.env.example` e rode `npm run start`, que sobe um servidor de exemplo junto com a tela. Esse
> servidor só atende a lista de fontes e o envio de arquivos; Quarentena, Imóveis rurais,
> Rastreabilidade, processamento e mapeamento só funcionam com o back-end real.

### Primeira carga de dados

Com o banco vazio e o Airflow ligado, carregue os dados nesta ordem — cada etapa depende da anterior:

1. **Cadastre as fontes por link:** rode uma vez o `db/seed-demo/fontes.sql` do back-end. Ele
   cadastra as fontes `MUN-IAT`, `CAR-WFS`, `RL-IAT` e `FOCOS-IAT` já com o mapeamento de colunas.
2. **Malha municipal do Paraná:** clique em **Executar** na fonte `MUN-IAT` e espere o arquivo
   ficar `PROCESSADO`. Sem ela, a lista de municípios fica vazia e a conferência "está no Paraná"
   não funciona.
3. **Imóveis do CAR:** **Executar** na fonte `CAR-WFS` (a coleta do Paraná inteiro demora).
4. **Reserva Legal e focos de calor:** **Executar** nas fontes `RL-IAT` e `FOCOS-IAT`. Os
   indicadores IRL e IFC são recalculados ao final de cada carga.
5. **Indicadores (opcional):** para recalcular na hora, na tela Imóveis rurais, selecione um
   imóvel e clique em **Calcular indicadores** (ou **Recalcular**). O cálculo é feito para todos os
   imóveis de uma vez.


## Como usar

### Visão geral da tela

A navegação fica no **menu lateral**, dividido em três grupos:

| Grupo | Tela | Para quê |
|---|---|---|
| Dados | **Fontes de dados** | Ver as fontes, importar arquivos, acompanhar o processamento e mapear colunas |
| Dados | **Quarentena** | Analisar e tratar os registros reprovados |
| Consulta | **Imóveis rurais** | Consultar os imóveis no mapa e na tabela, com os indicadores |
| Governança | **Rastreabilidade** | Ver de onde veio o número de um indicador |

No topo ficam o nome da tela e o botão **Modo claro / Modo escuro** (a escolha fica salva no
navegador). Em telas estreitas, o menu mostra só os ícones.

### Fontes de dados

Tela usada pelo Operador de Dados. À esquerda fica a lista **Fontes cadastradas**; à direita, o
card **Importar arquivo** e, abaixo dele, os **Arquivos recebidos** da fonte selecionada.

<img width="1600" height="805" alt="image" src="https://github.com/user-attachments/assets/a25dae79-09bd-482a-b740-e559fda8cff4" />


**Passo a passo:**

1. **Selecionar a fonte** na lista à esquerda. Cada fonte mostra sigla, nome, órgão, formato, o
   último arquivo recebido e o status (**Ativa**, **Sem arquivo** ou **Inativa**). Fontes coletadas
   por link têm a etiqueta "link". Um aviso âmbar "N nova(s)" indica colunas novas sem mapeamento
   (veja [Mapeamento de colunas](#mapeamento-de-colunas)).
2. **Enviar o arquivo:** arraste o arquivo para a área "Arraste o arquivo ou clique para
   selecionar", ou clique nela, e depois em **Iniciar importação**. Para escolher outro arquivo,
   use **Trocar arquivo**. A barra mostra o progresso do envio e, em seguida, "Processando no
   servidor…".
3. **Processamento automático:** arquivos `.zip`, `.geojson` e `.json` são enviados ao Airflow assim
   que o envio termina. Se o Airflow estiver fora do ar, a tela avisa, o arquivo fica como Recebido e
   basta clicar em **Processar** depois.
4. **Acompanhar o status** na lista **Arquivos recebidos**: Recebido, Processando, Processado,
   Rejeitado ou Aguardando mapeamento. Enquanto houver arquivo processando, a lista se atualiza
   sozinha a cada poucos segundos.
5. **Ver o resultado:** ao concluir, aparece "Processado pelo Airflow" ou "Rejeitado no
   processamento" (o motivo fica registrado na execução). Os registros inválidos ficam na
   [Quarentena](#quarentena).
6. **Processar de novo**, se necessário: arquivos Recebidos, Rejeitados ou Aguardando mapeamento
   mostram o botão **Processar**.
7. **Excluir** um arquivo rejeitado: o botão **Excluir** aparece só em arquivos Rejeitados. A
   exclusão apaga o arquivo do catálogo e da zona bruta e não pode ser desfeita.

**Cadastrar uma nova fonte:**

As fontes por link da demonstração (`MUN-IAT`, `CAR-WFS`, `RL-IAT`, `FOCOS-IAT`) são cadastradas
pelo seed (veja [Primeira carga de dados](#primeira-carga-de-dados)). Para cadastrar outra:

1. Clique em **Nova fonte** (o botão vira **Fechar** enquanto o formulário está aberto).
2. Preencha **Nome da fonte**, **Sigla** (até 12 caracteres, sem repetir uma existente),
   **Formato**, **Órgão responsável** e **Periodicidade**.
3. Em **Como os dados chegam**, escolha:
   - **Envio de arquivo** — os arquivos serão enviados pela área de importação; ou
   - **Link (coleta)** — o sistema baixa os dados de um endereço. Preencha o **Link de origem**
     (serviço ArcGIS, WFS/GeoServer ou um arquivo zip, CSV ou GeoJSON para baixar), o **Método**
     (GET ou POST), os **Cabeçalhos** em JSON, se o serviço exigir (não coloque senha nem token), e,
     no POST, o **Corpo da requisição**. Use **Testar link** para conferir se o endereço responde
     antes de salvar.
4. Clique em **Cadastrar fonte**.

**Coletar uma fonte por link:** clique no botão ▶ ("Executar agora: coletar o link") ao lado da
fonte. O arquivo baixado aparece em **Arquivos recebidos** como um envio comum e é processado em
seguida. Se o conteúdo for igual ao da última coleta, aparece "Sem mudança" e nada é gravado.

- O botão dispara a coleta no Airflow na hora; se ele estiver fora do ar, a tela avisa e é só tentar
  de novo. Enquanto a coleta roda, o botão fica girando e a mensagem de andamento aparece embaixo da fonte. Serviços ArcGIS e WFS
  são lidos em páginas; se faltar ou repetir algum registro, a coleta falha ("vieram X de Y") em
  vez de gravar dado pela metade.

### Mapeamento de colunas

<!-- TODO: print do modal "Mapear colunas" -->

Aberto pelo botão **#** ("Mapear colunas") ao lado de cada fonte, na tela Fontes de dados.

1. Em **Colunas detectadas**, veja as colunas que chegaram nos arquivos da fonte, com tipo e
   exemplos de valores. Cada uma aparece como **Mapeada**, **Nova** ou **Ausente**. As colunas só
   aparecem depois que o primeiro arquivo da fonte é lido.
2. Clique em **Mapear** numa coluna **Nova** e preencha:
   - **Tabela de destino** e **Campo de destino** (por exemplo, `imovel_rural` / `area_ha`);
   - **Tipo de dado**: texto, inteiro, decimal, data, booleano ou geometria;
   - **Regras gerais**: Obrigatório; Chave (identifica o registro — uma coluna chave é sempre
     obrigatória); Bloquear regressão (reprova o registro que chega vazio quando o valor atual
     está preenchido);
   - para **data**: o formato na origem (ex.: `dd/MM/yyyy`);
   - para **texto**: domínio de valores, conferência de existência em outra tabela (município ou
     imóvel) e validação de CPF/CNPJ;
   - para **geometria**: a política para geometria inválida — **Quarentena** (reprova e isola o
     registro) ou **Corrigir e registrar** — e se deve **conferir se está no Paraná** (totalmente
     fora reprova; parcialmente fora passa, sem recorte).
3. Clique em **Salvar mapeamento**.
4. Em **Colunas mapeadas**, use **Remover** para desfazer um mapeamento.
5. Depois de mapear, volte à lista de arquivos e clique em **Processar** nos arquivos que estavam
   aguardando mapeamento.

### Quarentena
Registros que não cumpriram alguma regra. O dado original nunca é alterado: a correção é guardada
à parte e validada de novo antes de ir para a zona tratada.

<img width="1365" height="673" alt="image" src="https://github.com/user-attachments/assets/8f04b449-8e96-450d-a1db-4067fe0a0dbf" />
<img width="1600" height="811" alt="image" src="https://github.com/user-attachments/assets/d79cefaf-438f-409a-b58a-af2a7bb02361" />


**Visão geral:**

- **Métricas:** Total retido, Pendentes, Em análise, Corrigidos, Revalidados e Descartados. Clicar
  num cartão filtra a lista por aquele status; clicar de novo desfaz o filtro.
- **Filtros:** Buscar (código, campo ou motivo), Tipo de erro, Fonte e Status. **Limpar** remove
  todos.
- **Tabela:** registro, erro, campo e motivo, origem (execução e linha do arquivo) e status, com 20
  registros por página.
- No fim da tela, o card **Os tipos de erro que a validação encontra** explica cada código de erro.

**Passo a passo para tratar um registro:**

1. Clique em **Analisar** na linha do registro. Abre uma gaveta com o erro (campo, valor recebido e
   motivo), uma explicação de **O que isso significa** e **Como resolver**, o dado original e o
   histórico do registro.
2. Clique em **Iniciar análise** (o status passa a **Em análise**).
3. Corrija, conforme o tipo de problema:
   - **Campos:** clique em **Corrigir manualmente**, edite a cópia de trabalho (em JSON, no mesmo
     formato do dado original) e clique em **Salvar correção**.
   - **Geometria:** no mapa da gaveta, o ponto do problema aparece em vermelho e a geometria como
     chegou, em ciano. Você pode:
     - **Corrigir automaticamente** — mostra uma prévia da correção em verde, que você pode
       ajustar;
     - **Editar vértices** — arraste os vértices; clique no meio de uma aresta para criar um e use
       o botão direito para apagar;
     - **Desenhar nova** — clique no mapa para marcar os vértices e no primeiro para fechar o
       polígono (use quando o registro chegou sem geometria);
     - **Desfazer** — volta à última versão salva.

     Depois, clique em **Salvar geometria**. O sistema recusa uma geometria que continue inválida e
     diz o motivo (por exemplo, "o contorno se cruza").
4. Clique em **Revalidar e aprovar**. O registro passa de novo por todas as regras:
   - se passar, é gravado na zona tratada e fica **Revalidado**;
   - se não passar, a gaveta lista os erros que continuam, para você corrigir de novo.
5. Se o registro não tiver como ser aproveitado, escreva a **Justificativa** e clique em
   **Descartar**. A justificativa é obrigatória.

Registros **Revalidados**, **Descartados** ou **Substituídos** ficam encerrados: só podem ser
consultados, e o histórico fica guardado para auditoria. Um registro fica **Substituído**
automaticamente quando uma carga mais nova traz o mesmo registro.

### Imóveis rurais

Tela usada pelo Analista para consultar e visualizar os imóveis já processados. Mostra o filtro no
topo, o mapa à esquerda (com a legenda de imóveis e focos de calor no cabeçalho) e, à direita, a
tabela de imóveis — com o total ao lado do título — e o card de indicadores.

<img width="1364" height="676" alt="image" src="https://github.com/user-attachments/assets/bd6eca35-016c-4642-8759-a0e524bc7145" />


**Passo a passo:**

1. **Filtrar** (opcional): escolha um **Município (PR)**, uma **Situação no CAR** (Ativo,
   Pendente, Cancelado, Suspenso) e/ou digite parte do **Código do imóvel**. Os filtros podem ser
   combinados; **Limpar filtro** remove todos. Ao escolher um município, o mapa se ajusta a ele e
   desenha o contorno tracejado.
2. **Consultar na tabela:** os imóveis aparecem com código, município, situação e área (ha), 50 por
   página. Use **Anterior** e **Próxima** para navegar.
3. **Consultar no mapa:** o mapa abre mostrando o Paraná inteiro e não sai dos limites do estado.
   Os imóveis só aparecem a partir de um certo zoom; antes disso, a mensagem "Aproxime o zoom para
   ver os imóveis" pede para aproximar. Se a área visível tiver imóveis demais, aparece "Mostrando
   parte dos imóveis — aproxime o zoom". Os **focos de calor** do INPE aparecem como pontos
   laranja; passe o mouse para ver o satélite e a data.
4. **Selecionar um imóvel:** clique numa linha da tabela ou num imóvel no mapa. A seleção é
   sincronizada: pela tabela, o mapa vai até o imóvel; pelo mapa, a linha é destacada. Um balão
   mostra código, município, área e situação. Clique de novo para desmarcar.
5. **Ver os indicadores** do imóvel selecionado no card **Indicadores do imóvel**, que mostra o
   código do imóvel logo abaixo do título:
   - **IRL · Reserva Legal** — percentual de Reserva Legal, numa barra de 0% a 40% com a marca do
     mínimo de 20%, e quantos hectares faltam ou sobram. Situação: **Conforme** ou **Crítico**;
   - **IFC · Focos de calor** — focos a cada 1.000 ha nos últimos 12 meses e o total de focos
     detectados no imóvel. Situação: **Sem focos**, **Alerta** ou **Crítico**;
   - cada indicador mostra, no rodapé, a regra e a versão usadas, as versões dos dados e quando foi
     calculado.

   Se os indicadores ainda não foram calculados, clique em **Calcular indicadores**. Depois de
   carregar dados novos, use **Recalcular**. O cálculo vale para todos os imóveis e pode demorar.
   Para ver como o número foi obtido, clique em **De onde veio este número?**.

### Rastreabilidade

Mostra o caminho completo de um indicador, do resultado até o arquivo de origem.
<img width="1362" height="670" alt="image" src="https://github.com/user-attachments/assets/bbff2b8a-9f8e-481c-b2ff-740328af29a8" />







