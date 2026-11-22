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
   - [Usuários de teste](#usuários-de-teste)
5. [Como usar](#como-usar)
   - [Entrar e sair](#entrar-e-sair)
   - [Perfis de acesso](#perfis-de-acesso)
   - [Visão geral da tela](#visão-geral-da-tela)
   - [Fontes de dados](#fontes-de-dados)
   - [Mapeamento de colunas](#mapeamento-de-colunas)
   - [Quarentena](#quarentena)
   - [Imóveis rurais](#imóveis-rurais)
   - [Indicadores](#indicadores)
   - [Rastreabilidade](#rastreabilidade)
   - [Usuários e perfis](#usuários-e-perfis)
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
- mostra a **rastreabilidade** de cada indicador: de qual arquivo, versão e regra veio o número;
- controla o **acesso por perfil** (Operador de dados, Analista, Auditor e Gestor), com login pelo
  Keycloak e a gestão dos perfis feita pelo próprio Gestor, numa tela do sistema.


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

#### RF08 — Login e controle de acesso por perfil

> Como pessoa usuária do sistema, quero entrar com meu usuário e senha e ver apenas o que o meu
> perfil permite, para que os dados e as operações fiquem protegidos.

- O sistema exige login para usar qualquer tela e qualquer rota da API. Sem um login válido, a API
  responde com erro 401 ("Autenticação necessária: envie um token válido.").
- O login é feito numa tela do **Keycloak**, com a identidade visual do GeoRural e em português. A
  senha não passa pelo front-end do sistema.
- Cada pessoa tem um ou mais **perfis**: Operador de dados, Analista, Auditor e Gestor.
- O menu mostra só as telas liberadas ao perfil. Quem digita o endereço de uma tela que o seu perfil
  não vê é levado a uma tela permitida. Quem não tem nenhum perfil vê "Sem acesso a nenhuma área".
- Os botões que alteram dados (cadastrar, importar, mapear, processar, corrigir, descartar,
  calcular) só aparecem para Operador de dados e Gestor.
- A API também barra no servidor: um perfil sem permissão recebe erro 403 ("Você não tem permissão
  para acessar este recurso."), mesmo que a chamada seja feita fora da tela.
- A sessão é renovada automaticamente enquanto a pessoa usa o sistema (cada acesso vale 15
  minutos). Quando a sessão termina, o sistema volta ao login.
- O topo da tela mostra o nome e o perfil de quem está conectado e o botão **Sair**.

#### RF09 — Gestão de usuários e perfis

> Como Gestor, quero ver os usuários do sistema e definir o que cada um pode usar, para controlar
> quem acessa cada área sem depender da equipe técnica.

- O sistema lista os usuários, com nome, usuário, e-mail e perfis, e permite buscar por nome,
  usuário ou e-mail. A lista é paginada (20 por página).
- O Gestor define os perfis de um usuário marcando ou desmarcando os quatro perfis do GeoRural. Só
  esses quatro perfis podem ser atribuídos por esta tela.
- O Gestor não pode remover o próprio perfil de Gestor, para não perder o acesso ao sistema.
- Um usuário sem nenhum perfil consegue entrar, mas não acessa nenhuma tela.
- A mudança de perfil vale no próximo login da pessoa (ou em até 15 minutos, se ela estiver
  conectada).
- Só o Gestor acessa esta tela e esta parte da API; os demais perfis recebem erro 403.
- Se o servidor de login (Keycloak) estiver fora do ar, a tela avisa que o serviço de autenticação
  está indisponível.

### Não funcionais

**Tecnologia e arquitetura**
- Back-end em Java 17 com Spring Boot; front-end em Vue 3 com Vite, Leaflet e Tailwind.
- Banco de dados Oracle; dados geoespaciais em colunas `SDO_GEOMETRY`, acessadas via Hibernate
  Spatial e JTS. As regras de qualidade, a gravação na zona tratada e o cálculo dos indicadores
  rodam em pacotes PL/SQL no próprio banco.
- Estrutura do banco versionada com Flyway (criada automaticamente na primeira subida).
- API REST em JSON; geometrias em GeoJSON.
- Autenticação e autorização com **Keycloak 26** (OpenID Connect), em Docker. O realm `georural`
  (perfis, usuários de teste, clients e tela de login) é importado do arquivo
  `keycloak/georural-realm.json`, no repositório do back-end.
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
- Toda rota da API exige um token válido do Keycloak. O back-end valida o token (Spring Security
  como *resource server*) e libera cada rota só para os perfis da matriz de permissões.
- O login do front-end usa *Authorization Code com PKCE* (keycloak-js); o token fica só em memória
  e a senha nunca passa pelo sistema.
- Os perfis são *realm roles* do Keycloak (`OPERADOR_DADOS`, `ANALISTA`, `AUDITOR`, `GESTOR`) e
  vão dentro do token. O que o front-end esconde (menu, telas e botões) é só conforto: quem barra
  de verdade é o back-end.
- Para gerenciar usuários, o back-end usa uma conta técnica (`georural-backend`) com as permissões
  mínimas do Keycloak para consultar e alterar usuários. A gestão só aceita os quatro perfis do
  GeoRural e é restrita ao Gestor.

**Usabilidade e confiabilidade**
- As mensagens de erro da API vêm em português, com o código HTTP apropriado.
- O andamento do processamento é atualizado automaticamente na tela, sem recarregar a página.
- A interface tem modo claro e modo escuro.
- A tela de login tem a identidade visual do GeoRural e está em português do Brasil.

**Manutenibilidade**
- Código versionado no GitHub, com o fluxo de branches `feature → dev → main` e revisão por Pull
  Request.
- O back-end segue arquitetura em camadas (`controller` → `service` → `repository`).
- A matriz de permissões por perfil vive em dois lugares que precisam andar juntos: `SecurityConfig`
  (back-end) e `router/index.js` com `usePermissoes.js` (front-end).
- A memória de cálculo dos indicadores, com exemplos reais e o passo a passo de conferência no
  QGIS, está em `docs/memoria-de-calculo/` no repositório do back-end.


## Como instalar e rodar

O GeoRural é formado por dois projetos: o **back-end** (a API) e o **front-end** (a tela). Os dois
precisam estar rodando para usar a aplicação por completo.

### Back-end

**Pré-requisitos:** JDK 17, Docker Desktop, Git e uma credencial válida da Oracle Cloud (OCI) para
o Object Storage.

1. Clone o repositório `GeoRural-DataHub-BackEnd`.
2. Copie o `.env.example` para `.env`, na raiz do projeto, e preencha `OCI_NAMESPACE`,
   `OCI_BUCKET`, `DB_USERNAME` e `DB_PASSWORD`. Para processar e coletar, suba também o Airflow
   (`infra/airflow/README.md` deste repositório). O login usa valores padrão de desenvolvimento;
   para trocá-los, veja a observação depois do passo 6.
3. Configure a credencial da OCI em `~/.oci/config`, no perfil `DEFAULT`. **Sem esse arquivo a
   aplicação não inicia**, mesmo que você não vá enviar arquivos.
4. Suba o banco e o servidor de login (Keycloak): `docker compose up -d`. A primeira subida demora
   alguns minutos; o banco está pronto quando `docker logs oracle` mostrar
   `DATABASE IS READY TO USE!`. O Keycloak fica em `http://localhost:8081` e importa sozinho o
   realm `georural` (perfis, usuários de teste e tela de login) quando o container é criado. O
   painel de administração dele é `http://localhost:8081/admin/master/console/`, com usuário e
   senha padrão de desenvolvimento `admin` / `admin`.
5. Rode a aplicação: `mvnw.cmd spring-boot:run` (Windows) ou `./mvnw spring-boot:run`. As tabelas
   são criadas automaticamente na primeira subida.
6. A API fica disponível em `http://localhost:8080/api/v1`.

> **Login (Keycloak).** Valores que podem ser definidos no `.env`: `KEYCLOAK_URL` (padrão
> `http://localhost:8081`), `KEYCLOAK_BACKEND_SECRET` (segredo da conta técnica do back-end),
> `KEYCLOAK_ADMIN` e `KEYCLOAK_ADMIN_PASSWORD` (administrador do painel). Os padrões servem só para
> desenvolvimento.
>
> **Recriar o Keycloak** (para aplicar uma mudança em `keycloak/georural-realm.json` ou no tema de
> login): `docker rm -f keycloak` e depois `docker compose up -d --no-deps keycloak`. O realm só é
> importado quando o container é criado. **Nunca use `docker compose down -v`**: ele apaga também o
> volume do banco Oracle.

### Front-end

**Pré-requisitos:** Node.js 18 ou mais recente e npm 9 ou mais recente.

1. Clone o repositório `GeoRural-DataHub-FrontEnd`.
2. Instale as dependências: `npm install`.
3. Copie o `.env.example` para `.env.development`.
4. Edite o `.env.development` e defina, com o back-end e o Keycloak já rodando:
   - `VITE_API_URL=http://localhost:8080/api/v1`
   - `VITE_KEYCLOAK_URL=http://localhost:8081`
   - `VITE_KEYCLOAK_REALM=georural`
   - `VITE_KEYCLOAK_CLIENT_ID=georural-frontend`
5. Rode `npm run dev`. O navegador abre sozinho em `http://localhost:5173` e vai para a tela de
   login (veja [Entrar e sair](#entrar-e-sair)).

> **Só para testar o envio de arquivos, sem o back-end:** mantenha o `VITE_API_URL` do
> `.env.example` e rode `npm run start`, que sobe um servidor de exemplo junto com a tela. Esse
> servidor só atende a lista de fontes e o envio de arquivos; Quarentena, Imóveis rurais,
> Rastreabilidade, processamento e mapeamento só funcionam com o back-end real. Como esse servidor não valida o login, defina também
> `VITE_AUTH_HABILITADO=false` no `.env.development`.

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


### Usuários de teste

O realm importado traz quatro usuários para o ambiente de desenvolvimento, todos com a senha
`georural123`:

| Usuário | Perfil |
|---|---|
| `operador` | Operador de dados |
| `analista` | Analista |
| `auditor` | Auditor |
| `gestor` | Gestor |

> **Só para desenvolvimento.** Em um ambiente real, troque ou remova esses usuários e defina uma
> senha própria para o administrador do Keycloak.


## Como usar

### Entrar e sair

<!-- TODO: print da tela de login -->

Ao abrir o sistema (`http://localhost:5173`), se você ainda não estiver conectado, o navegador vai
para a tela de **login** do GeoRural DataHub. Ela é do Keycloak, o servidor de identidade do
sistema: a sua senha não passa pelo GeoRural.

1. Digite o **Usuário ou e-mail** e a **Senha**.
2. Clique em **Entrar**. Com os dados corretos, o sistema abre na primeira tela liberada ao seu
   perfil.
3. Se os dados estiverem errados, a tela avisa e pede de novo. A recuperação de senha por e-mail não
   está ativada: se esqueceu a senha, procure um Gestor.
4. No topo da tela ficam o seu nome e o seu perfil (por exemplo, "Gestor"). Clique em **Sair** para
   encerrar a sessão; o sistema volta ao login.
5. Enquanto você usa o sistema, a sessão é renovada sozinha. Se ela terminar, o sistema volta ao
   login e basta entrar de novo.

### Perfis de acesso

Cada pessoa tem um ou mais perfis, que definem o que ela vê e o que pode fazer:

| Perfil | Em resumo |
|---|---|
| **Operador de dados** | Importa e processa dados: fontes, arquivos, mapeamentos, quarentena e cálculo de indicadores |
| **Analista** | Consulta o mapa, os imóveis, os indicadores e a rastreabilidade, sem alterar dados |
| **Auditor** | Acompanha fontes, quarentena, indicadores e rastreabilidade, sem alterar dados |
| **Gestor** | Acesso total, inclusive a gestão de usuários e perfis |

O que cada perfil acessa (✔ = pode, — = não aparece):

| Área | Operador de dados | Analista | Auditor | Gestor |
|---|:---:|:---:|:---:|:---:|
| Fontes de dados: consultar | ✔ | — | ✔ | ✔ |
| Fontes de dados: importar, coletar, processar, mapear colunas, excluir | ✔ | — | — | ✔ |
| Quarentena: consultar | ✔ | — | ✔ | ✔ |
| Quarentena: analisar, corrigir, revalidar, descartar | ✔ | — | — | ✔ |
| Imóveis rurais: mapa, tabela e indicadores | ✔ | ✔ | ✔ | ✔ |
| Imóveis rurais: calcular e recalcular indicadores | ✔ | — | — | ✔ |
| Rastreabilidade | ✔ | ✔ | ✔ | ✔ |
| Usuários e perfis | — | — | — | ✔ |

- O **menu** mostra só as telas do seu perfil. Os botões que alteram dados (por exemplo **Processar**,
  **Excluir**, **Salvar mapeamento**) só aparecem para Operador de dados e Gestor.
- Se você digitar o endereço de uma tela que o seu perfil não vê, o sistema leva você a uma tela
  permitida.
- Se aparecer **"Sem acesso a nenhuma área"**, a sua conta entrou, mas ainda não tem nenhum perfil.
  Peça a um Gestor para atribuir um (veja [Usuários e perfis](#usuários-e-perfis)) e entre de novo.

### Visão geral da tela

A navegação fica no **menu lateral**, dividido em grupos. Você vê só as telas liberadas ao seu
perfil (veja [Perfis de acesso](#perfis-de-acesso)):

| Grupo | Tela | Para quê |
|---|---|---|
| Dados | **Fontes de dados** | Ver as fontes, importar arquivos, acompanhar o processamento e mapear colunas |
| Dados | **Quarentena** | Analisar e tratar os registros reprovados |
| Consulta | **Imóveis rurais** | Consultar os imóveis no mapa e na tabela, com os indicadores |
| Governança | **Indicadores** | Cadastrar, calcular, ligar/desligar indicadores e ver o resultado por município |
| Governança | **Rastreabilidade** | Ver de onde veio o número de um indicador |
| Administração | **Usuários e perfis** | Definir o que cada pessoa pode usar (somente Gestor) |

No topo ficam o nome da tela, o seu nome e perfil, o botão **Modo claro / Modo escuro** (a escolha
fica salva no navegador) e o botão **Sair**. Em telas estreitas, o menu mostra só os ícones.

### Fontes de dados

Tela usada pelo Operador de Dados. Mostra as fontes em cards; clicar num card abre a página da
fonte, com as abas **Recebimento**, **Arquivos recebidos**, **Mapeamento** e **Versões**.

<!-- TODO: print novo da lista de fontes (cards) e da página da fonte com as abas -->
<img width="1600" height="805" alt="image" src="https://github.com/user-attachments/assets/a25dae79-09bd-482a-b740-e559fda8cff4" />

**A lista de fontes:**

- **Filtros no topo:** Todas, Ativas, Coleta automática, Upload e Desativadas, com a quantidade de
  cada uma, e a busca por nome, sigla ou órgão.
- **Cada card** mostra a sigla, a etiqueta **Coleta automática** (link) ou **Upload** (envio de
  arquivo), o formato, o nome, o órgão, a data da última coleta ou envio e quantas colunas estão
  **sem mapeamento**.
- **A chave no canto do card** ativa ou desativa a fonte. Uma fonte **desativada** não recebe
  arquivos nem coletas até ser religada; os dados que ela já gravou **continuam valendo** (não são
  apagados).
- **Grupos de fontes:** juntam cards numa "pasta" (ex.: uma camada que veio em vários pedaços,
  cada um numa fonte). O grupo **só organiza a tela**: não muda o processamento nem os indicadores.

**Cadastrar uma nova fonte:**

As fontes da demonstração (`MUN-IAT`, `CAR-WFS`, `RL-IAT`, `FOCOS-IAT`) são cadastradas pelo seed
(veja [Primeira carga de dados](#primeira-carga-de-dados)). Para cadastrar outra, clique em
**Nova fonte** e preencha:

| Campo | Como preencher |
|---|---|
| **Nome da fonte** | Nome por extenso, como aparece nos cards e nos indicadores. Ex.: "Terras Indígenas no Paraná (FUNAI)". |
| **Sigla** | Curta, em maiúsculas, sem repetir outra fonte (ex.: `TI-FUNAI`). **Não muda depois**: é o nome da pasta dos arquivos da fonte na zona bruta. |
| **Formato** | O que o arquivo ou o link **devolve**: **CSV**, **GeoJSON** ou **SHP**. Um serviço ArcGIS ou WFS com `f=geojson` ou `outputFormat=application/json` é **GeoJSON**, mesmo sendo um link. Formato errado faz a carga ler o conteúdo errado. |
| **Órgão responsável** | Quem publica o dado. Quando ele vem por outro órgão, diga os dois: "FUNAI (via IAT/PR)". |
| **Periodicidade** | De quanto em quanto tempo o dado muda na origem (diária, mensal, anual…). É informativa. |
| **Como os dados chegam** | **Envio de arquivo**: alguém sobe o arquivo pela tela. **Link (coleta)**: o sistema baixa do endereço do órgão. Prefira o link quando ele existir: a fonte se atualiza sem ninguém precisar baixar e subir arquivo. |
| **Link de origem** (só no link) | Endereço do serviço ArcGIS (`.../FeatureServer/0/query?where=1=1&outFields=*&f=geojson&outSR=4674`), do WFS/GeoServer (`...?service=WFS&request=GetFeature&typeNames=...&outputFormat=application/json`) ou de um arquivo zip, CSV ou GeoJSON. Para pegar só uma parte, filtre no próprio link (ex.: `CQL_FILTER=state%3D%27PR%27` no WFS, `where=` no ArcGIS). |
| **Método** | Quase sempre **GET**. POST só se o serviço exigir; aí aparece o **Corpo da requisição**. |
| **Cabeçalhos (JSON, opcional)** | Só se o serviço pedir (ex.: `{"Accept": "application/json"}`). **Nunca** coloque senha nem token: os cabeçalhos ficam gravados junto com a fonte. |

Antes de salvar uma fonte por link, clique em **Testar link**: a tela diz se o endereço respondeu,
quantos registros e quais colunas vieram.

> **Formatos de arquivo aceitos:** shapefile num `.zip` (com `.shp`, `.shx`, `.dbf` e `.prj`),
> GeoJSON (`.geojson` ou `.json`) e CSV com a geometria numa coluna de texto WKT
> (`POINT (-51.2 -25.4)`, `POLYGON (...)`) ou em duas colunas de latitude e longitude.

**Ordem certa para uma fonte nova:**

1. Cadastre a fonte e **teste o link** (ou envie o primeiro arquivo).
2. Vá na aba **Mapeamento** e mapeie as colunas **antes** de coletar ou processar (veja
   [Mapeamento de colunas](#mapeamento-de-colunas)). As colunas aparecem depois do teste do link ou
   do primeiro arquivo.
3. Só então clique em **Executar agora** (link) ou processe o arquivo.

Se a carga rodar antes do mapeamento, o arquivo fica **Aguardando mapeamento** ou os registros vão
todos para a quarentena; depois de mapear, use **Reprocessar** (veja abaixo).

**Aba Recebimento:**

- **Fonte por link:** mostra o link, a última coleta e a paginação. **Executar agora** baixa os
  dados na hora. Serviços ArcGIS e WFS são lidos **em páginas** até o fim, conferindo o total; se
  faltar ou repetir algum registro, a coleta falha ("vieram X de Y") em vez de gravar dado pela
  metade. Se o conteúdo for igual ao da última coleta, a execução fica como **sem mudança** e nada é
  reprocessado: por isso, **coletar de novo não corrige um mapeamento** (use **Reprocessar**).
- **Fonte por envio de arquivo:** arraste o arquivo para a área de envio, ou clique nela, e depois
  em **Iniciar importação**. Arquivos `.zip`, `.geojson`, `.json` e `.csv` vão para o Airflow assim
  que o envio termina. Se o Airflow estiver fora do ar, a tela avisa e o arquivo fica como
  Recebido, para clicar em **Processar** depois.

**Aba Arquivos recebidos:**

1. Cada arquivo mostra nome, tamanho, data, se veio por coleta ou envio, e o status: **Recebido**,
   **Processando**, **Processado**, **Rejeitado** ou **Aguardando mapeamento**. Enquanto houver
   arquivo processando, a lista se atualiza sozinha e mostra a barra de progresso.
2. **Processar**: aparece nos arquivos Recebidos, Rejeitados ou Aguardando mapeamento.
3. **Reprocessar**: aparece só no arquivo **Processado mais recente** da fonte (é ele que está
   valendo). Use depois de mudar o mapeamento: o arquivo é lido de novo, sem precisar baixar ou
   subir outra vez, e os dados atuais da fonte são trocados pelos da nova leitura.
4. **Excluir**: só em arquivos Rejeitados. Apaga o arquivo do catálogo e da zona bruta e não pode
   ser desfeito.
5. Os registros reprovados ficam na [Quarentena](#quarentena). Para ver **quantos** foram válidos e
   inválidos, confira as execuções da fonte: se **todos** foram reprovados, quase sempre é um campo
   mapeado do jeito errado (abra um registro na Quarentena para ver o motivo).

**Aba Versões:** cada carga processada gera uma versão dos dados da fonte, usada na rastreabilidade
dos indicadores.

### Mapeamento de colunas

<!-- TODO: print da aba Mapeamento (colunas detectadas e mapeadas) -->

Fica na aba **Mapeamento** da página da fonte. O mapeamento diz, coluna por coluna, para onde vai
cada dado e que regras ele precisa cumprir. **Só o que está mapeado é gravado**; as colunas sem
mapeamento não se perdem: ficam guardadas como atributos de cada registro na zona bruta e na
quarentena.

1. Em **Colunas detectadas**, veja as colunas que chegaram, com tipo percebido e exemplos. Cada uma
   aparece como **Mapeada**, **Nova** ou **Ausente**.
2. Clique em **Mapear** numa coluna e preencha os campos (tabela abaixo).
3. Salve. A coluna passa para **Colunas mapeadas**, onde dá para **Editar** ou **Remover**.
4. Se houver arquivo **aguardando mapeamento**, aparece uma faixa amarela com o botão para
   reprocessar todos. Se o arquivo já tinha sido processado, use **Reprocessar** na aba
   **Arquivos recebidos**.

**Campos do mapeamento:**

| Campo | Como preencher |
|---|---|
| **Tabela de destino** | Onde o dado vai morar. As tabelas próprias (`imovel_rural`, `car_reserva_legal`, `foco_calor`, `municipio`) são das fontes do CAR, dos focos e dos municípios. **Qualquer outra fonte** (desmatamento, áreas protegidas, embargos…) vai para **`input_feature`**, o destino genérico que os indicadores cadastrados pela tela usam. |
| **Campo de destino** | O campo da tabela. Na `input_feature`: **`geometria`** (o desenho), **`categoria`** (um texto para filtrar no indicador, ex.: a classe "DESMATAMENTO"), **`data_referencia`** (a data da observação, usada no filtro "A partir de") e **`valor`** (um número declarado pela fonte). Um campo já usado por outra coluna aparece desabilitado. |
| **Tipo de dado** | O tipo do **valor que vem da origem**: TEXTO, INTEIRO, DECIMAL, DATA, BOOLEANO ou GEOMETRIA. O valor que não converte vai para a quarentena ("Valor não converte para DATA", por exemplo). |
| **Obrigatório** | Marque quando o registro não serve sem esse valor; vazio vai para a quarentena. Se a tela avisar "A tabela não aceita este campo vazio", **marque**: sem isso, um valor vazio derruba a carga inteira em vez de ir para a quarentena. **A geometria é sempre obrigatória.** |
| **Chave (identifica o registro)** | A coluna que diz "é o mesmo registro" de uma carga para a outra (ex.: `cod_imovel` no CAR, o código IBGE nos municípios). Com chave, a carga atualiza o que mudou; sem chave, cada carga substitui a anterior inteira. Uma chave é sempre obrigatória. Na dúvida, **não marque**. |
| **Bloquear regressão** | Reprova o registro que chega vazio num campo que hoje está preenchido (evita apagar um dado bom com uma carga ruim). |

**Só para GEOMETRIA:**

| Campo | Como preencher |
|---|---|
| **Tipo esperado** | **Polígono** para áreas (imóvel, reserva legal, município, desmatamento, unidade de conservação…), **Ponto** para qualquer ponto (focos de calor e outros) e **Linha** para rios e estradas. Polígono aceita multipolígono, e assim por diante. Uma geometria de outro tipo é reprovada. |
| **Política para geometria inválida** | **Corrigir e registrar** (recomendado): o sistema conserta o desenho (contorno que se cruza, ponto repetido…), grava e anota a correção. **Quarentena**: reprova e isola o registro para alguém corrigir à mão. |
| **Conferir se está no Paraná** | Marque nas fontes do PR: um desenho **totalmente fora** do estado é reprovado; **parcialmente fora** passa, sem recorte. Depende da malha de municípios carregada (`MUN-IAT`); sem ela, a carga avisa e não valida. Não marque na própria fonte de municípios. |

**Só para DATA:**

| Campo | Como preencher |
|---|---|
| **Formato da data na origem** | A máscara do jeito que a data vem. **Vazio vale `DD/MM/YYYY`.** Exemplos: `2006-09-12` → `YYYY-MM-DD`; `2015/03/11 17:19:00` → `YYYY/MM/DD HH24:MI:SS`. Se a coluna mistura formatos, separe com `\|` (vale o primeiro que converter). Milissegundos desde 1970 (comum no ArcGIS) → `EPOCH_MS`. **Formato errado reprova todos os registros** ("Valor não converte para DATA"): confira um exemplo da coluna antes. |

**Só para DECIMAL:** marque **"A origem usa vírgula como separador decimal"** quando o número vem
como `25,9`.

**Só para TEXTO:**

| Campo | Como preencher |
|---|---|
| **Domínio de valores** | Lista dos valores aceitos (ex.: situação do CAR). Valor fora da lista vai para a quarentena. |
| **Conferir existência em** | Confere se o valor existe em outra tabela (município ou imóvel). Só **valida**: não liga o registro a ela. |
| **Validar CPF/CNPJ** | Reprova documentos com dígito verificador errado. |

**Exemplos que funcionaram:**

- **Desmatamento PRODES (link WFS do INPE)** para o IDESMAT: `geometry` → `input_feature.geometria`
  (Polígono, obrigatório, Corrigir e registrar, conferir no PR); `main_class` →
  `input_feature.categoria` (TEXTO, obrigatório); `image_date` → `input_feature.data_referencia`
  (DATA, obrigatório, formato `YYYY-MM-DD`).
- **Camadas só de desenho** (terras indígenas, unidades de conservação, quilombolas, embargos):
  basta a `geometry` → `input_feature.geometria`, com as mesmas opções acima.
- **CSV de pontos:** a coluna da geometria (WKT ou latitude/longitude) como GEOMETRIA **Ponto**
  obrigatória; uma área com vírgula como DECIMAL com a caixinha da vírgula → `valor`.

### Quarentena
Registros que não cumpriram alguma regra. O dado original nunca é alterado: a correção é guardada
à parte e validada de novo antes de ir para a zona tratada.

<img width="1365" height="673" alt="image" src="https://github.com/user-attachments/assets/8f04b449-8e96-450d-a1db-4067fe0a0dbf" />
<img width="1600" height="811" alt="image" src="https://github.com/user-attachments/assets/d79cefaf-438f-409a-b58a-af2a7bb02361" />


**Visão geral:**

- **Métricas:** Total retido, Pendentes, Em análise, Corrigidos, Revalidados e Descartados. Os
  Substituídos ficam guardados, mas não contam no total nem aparecem na lista geral. Registros sem
  geometria e com área declarada 0 já chegam como Descartados, com a justificativa. Clicar
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

### Indicadores

<!-- TODO: print da lista de indicadores e do formulário "Novo indicador" -->

Tela em **Governança > Indicadores**. Aqui ficam os indicadores montados a partir de uma fonte de
dados, de um modelo de cálculo pronto e de faixas de status. O **IRL** e o **IFC** são fixos: não
aparecem nesta lista e são vistos no card do imóvel (veja [Imóveis rurais](#imóveis-rurais)).

**A lista:** sigla, nome, modelo, unidade, fonte, data de cadastro e situação. Dá para buscar por
sigla ou nome, filtrar por fonte e ver Todos, Ativos ou Inativos.

- **Chave Ativo/Inativo:** um indicador inativo não é mais calculado, mas os resultados já
  calculados continuam valendo e podem ser consultados.
- **Calcular:** calcula o indicador para todos os imóveis (cerca de 559 mil). Uma barra mostra o
  andamento ("Gravando os resultados 94%"); leva alguns minutos. Só quem opera dados vê o botão, e
  só em indicador ativo.
- **Por município:** abre o resultado mais recente de cada imóvel agrupado pelo município: quantos
  imóveis, quantos em cada status, a média simples do valor e a **soma** do numerador (hectares
  cobertos, focos…). Os números seguem o formato brasileiro: `1.973.144,881` são cerca de 1,97
  milhão. Imóvel sem município aparece na linha **Sem município**.

**Cadastrar um indicador:** clique em **+ Novo indicador** e preencha. **Cadastrar não calcula**:
o cálculo é um passo à parte (botão **Calcular**).

| Campo | Como preencher |
|---|---|
| **Sigla** | De 2 a 10 letras maiúsculas ou números, começando com letra (ex.: `IDESMAT`). **Não pode repetir**, nem a de um indicador desativado. |
| **Nome** e **Unidade** | Nome por extenso e a unidade do valor: `%` na cobertura de área, `focos/1000` na densidade de pontos. |
| **Descrição** (opcional) | Uma frase dizendo o que o número mede. Ajuda quem lê o resultado depois. |
| **Fonte de dados** | A fonte principal. Ela precisa **já ter dados processados** no destino genérico (`input_feature`), com a geometria mapeada; senão aparece "A fonte N ainda não tem dados processados no insumo genérico". |
| **Modelo de cálculo** | Veja a tabela de modelos abaixo. O tipo de geometria da fonte tem que combinar com o modelo. |
| **Somar outras fontes** (opcional) | Para um indicador feito de várias camadas (ex.: áreas protegidas = UC + terra indígena + quilombola). Na cobertura, a área coberta por mais de uma fonte conta **uma vez só**. Todas precisam ter o mesmo tipo de geometria da principal. |
| **Pontos a cada quantos hectares** | Só na densidade de pontos: a base da conta (ex.: `1000` → focos a cada 1.000 ha). |
| **Só a categoria** (opcional) | Usa só as feições cuja `categoria` é igual a este texto, **exatamente como vem na fonte** (maiúsculas contam; ex.: `DESMATAMENTO`). Vazio = todas as feições, sem filtro. |
| **A partir de** (opcional) | Usa só as feições com `data_referencia` nesse dia ou depois (ex.: desmatamento após 22/07/2008). A fonte precisa ter a data mapeada em `data_referencia`. Vazio = sem filtro. |
| **Mínimo exigido (%)** (opcional) | Só na cobertura de área, quando **mais é melhor**: quanto do imóvel a lei exige (ex.: 20% de Reserva Legal no Paraná). Com ele, o imóvel mostra o déficit ou o excedente em hectares. Em indicador onde **qualquer valor é ruim** (desmatamento, embargo), deixe vazio. |
| **Faixas de status** | Em **ordem crescente** de valor. Cada faixa tem um status (Conforme, Alerta, Crítico ou Validação), um limite e a opção **inclui o limite**. A **última vale para tudo acima** da anterior e não tem limite. |

**Modelos de cálculo:**

| Modelo | O que calcula | A fonte precisa ter | Exemplo |
|---|---|---|---|
| **Densidade de pontos** | Pontos dentro do imóvel, por área | Só pontos | Focos de calor a cada 1.000 ha |
| **Cobertura de área** | Quanto do imóvel (em %) é coberto pelos polígonos da fonte | Só polígonos | Desmatamento, áreas protegidas, embargos, vegetação nativa |
| **Cobertura dentro de uma camada** | Quanto de uma **camada base** dentro do imóvel é coberto pela fonte | Só polígonos, nas duas | APP conservada = APP com vegetação nativa ÷ APP total |

Na **cobertura dentro de uma camada**, a fonte é a cobertura (ex.: vegetação) e a **Camada base**
(o denominador, ex.: APP) é marcada num bloco próprio. Se a base veio em vários pedaços (APP,
APP02, APP03…), marque todos: o cálculo junta. Imóvel sem a camada base não recebe resultado.

**Como pensar as faixas:**

- **Qualquer valor acima de zero é problema** (desmatamento, sobreposição com área protegida,
  embargo): **Conforme** até `0` (inclui o limite) e **Crítico** acima.
- **Mais é melhor** (vegetação nativa, reserva legal): **Crítico** até o mínimo (sem incluir o
  limite) e **Conforme** acima; preencha também o **Mínimo exigido**.
- Para uma zona de atenção, acrescente uma faixa **Alerta** entre as duas.

**Exemplos de indicadores cadastrados:**

| Sigla | Fonte(s) | Modelo | Filtros | Faixas |
|---|---|---|---|---|
| `IDESMAT` | Desmatamento PRODES (`MTA`) | Cobertura de área | Categoria `DESMATAMENTO`, a partir de 22/07/2008 | Conforme até 0 · Crítico acima |
| `ISAP` | `TI-FUNAI` + `UC-EST`, `UC-FED`, `QUIL-INCRA` | Cobertura de área | — | Conforme até 0 · Crítico acima |
| `IAE` | `EMB-IBAMA` + `EMB-ICMBIO` | Cobertura de área | — | Conforme até 0 · Crítico acima |

> **Um indicador não pode ser editado.** Para mudar a regra, desative o indicador e cadastre outro
> com uma sigla nova. Confira tudo (principalmente a categoria e a data) antes de salvar.

### Rastreabilidade

Mostra o caminho completo de um indicador, do resultado até o arquivo de origem.
<img width="1362" height="670" alt="image" src="https://github.com/user-attachments/assets/bbff2b8a-9f8e-481c-b2ff-740328af29a8" />


### Usuários e perfis

<!-- TODO: print da tela Usuários e perfis e da gaveta de edição -->

Tela usada pelo Gestor, em **Administração > Usuários e perfis**, para definir o que cada pessoa pode
usar no sistema. Os demais perfis não veem este item no menu.

**Passo a passo:**

1. Abra **Administração > Usuários e perfis**. A lista mostra o nome, o usuário, o e-mail e os
   perfis de cada pessoa, em etiquetas. Quem não tem nenhum perfil aparece como **Sem perfil**, e
   contas desativadas trazem a marca "desativado".
2. **Buscar:** digite no campo "Buscar por nome, usuário ou e-mail". A lista se atualiza sozinha
   depois que você para de digitar. **Atualizar** recarrega a lista.
3. A lista mostra 20 pessoas por página; use **Anterior** e **Próxima** para navegar.
4. Clique em **Editar perfis** na linha da pessoa. Abre uma gaveta à direita com os quatro perfis, cada
   um com uma explicação do que permite:
   - **Operador de dados:** importa e processa dados (fontes, arquivos, mapeamentos, quarentena e
     indicadores);
   - **Analista:** consulta o mapa, os imóveis e os indicadores, sem alterar dados;
   - **Auditor:** acompanha fontes, quarentena e rastreabilidade, sem alterar dados;
   - **Gestor:** acesso total, inclusive a gestão de usuários.
5. Marque ou desmarque os perfis. O botão **Salvar perfis** só fica ativo depois de uma mudança.
   **Cancelar**, o **✕** e a tecla **Esc** fecham a gaveta sem salvar.
6. Clique em **Salvar perfis**. A gaveta fecha, a linha da pessoa mostra os novos perfis e aparece o
   aviso "Perfis de … atualizados".

**Avisos importantes:**

- **A sua própria linha:** o perfil **Gestor** fica travado. Você não pode remover o seu próprio
  perfil de Gestor, para não perder o acesso ao sistema. Para isso, peça a outro Gestor.
- **Sem nenhum perfil:** a gaveta avisa que a pessoa consegue entrar, mas não acessa nenhuma tela.
- **Quando a mudança vale:** no próximo login da pessoa, ou em até 15 minutos se ela estiver
  conectada. Peça a ela para sair e entrar de novo, se precisar da mudança na hora.
- Esta tela **não cria** usuários nem altera senhas; veja [Limitações conhecidas](#limitações-conhecidas).


## Mensagens comuns e o que fazer

| Mensagem | O que significa | O que fazer |
|---|---|---|
| Volta para a tela de login, ou "Autenticação necessária: envie um token válido." | A sessão terminou | Entre de novo |
| "Você não tem permissão para acessar este recurso." | O seu perfil não libera esta ação | Peça a um Gestor o perfil adequado |
| "Sem acesso a nenhuma área" | A conta entrou, mas não tem perfil | Peça a um Gestor para atribuir um perfil e entre de novo |
| Credenciais inválidas na tela de login | Usuário ou senha incorretos | Confira os dados; se esqueceu a senha, procure um Gestor |
| "Não foi possível falar com o servidor. Ele está rodando?" | O back-end não respondeu | Confirme que o back-end está no ar (veja [Como instalar e rodar](#como-instalar-e-rodar)) |
| "Você não pode remover o seu próprio perfil de Gestor." | Tentativa de tirar o próprio perfil de Gestor | Peça a outro Gestor, se for mesmo necessário |
| "O serviço de autenticação está indisponível. Tente novamente em instantes." | O Keycloak está fora do ar | Confirme que o container `keycloak` está rodando e tente de novo |
| "Usuário não encontrado" | A pessoa não existe mais no Keycloak | Atualize a lista; confirme com a equipe técnica |
| "A fonte N ainda não tem dados processados no insumo genérico." (Novo indicador) | A fonte ainda não gravou nada em `input_feature`: nunca foi processada, foi processada sem mapeamento ou teve todos os registros reprovados | Confira o mapeamento (a `geometry` precisa estar mapeada), use **Reprocessar** e veja os válidos da execução |
| Execução com **0 válidos** e todos inválidos | Uma regra do mapeamento barra todo mundo | Abra um registro da fonte na **Quarentena** e leia o motivo; corrija o mapeamento e use **Reprocessar** |
| "Valor não converte para DATA." (Quarentena) | O formato da data na origem não bate com a máscara do mapeamento (vazio = `DD/MM/YYYY`) | Edite a coluna no mapeamento e ponha a máscara certa (ex.: `YYYY-MM-DD`); depois **Reprocessar** |
| Execução "sem mudança" depois de corrigir o mapeamento | A coleta compara o conteúdo do link, não o mapeamento | Use **Reprocessar** no arquivo, em vez de **Executar agora** |
| "Por município" mostra quase tudo em **Sem município** | Os imóveis estão sem o município preenchido | Rode a DAG `calcular_indicadores` (ela preenche o município pelo código do CAR) |


## Limitações conhecidas

- **Criar usuários, desativá-los ou redefinir senhas** é feito no painel de administração do Keycloak
  (`http://localhost:8081/admin/master/console/`), não pela tela **Usuários e perfis**, que só
  define perfis de quem já existe.
- A mudança de perfil vale no **próximo login** (ou em até 15 minutos).
- **Indicador não tem edição** e a sigla não pode ser reaproveitada: para mudar uma regra, é preciso
  desativar e cadastrar outro.
- Os seletores de fonte do **Novo indicador** e do filtro da **Quarentena** ainda listam fontes
  desativadas: escolha só fontes ativas.
- O **Testar link** de um serviço ArcGIS com mais de 2.000 registros avisa que "a coleta faz um
  pedido só". O aviso está desatualizado: a coleta lê em páginas e traz tudo.
- Os perfis liberam telas e ações **para todos os dados**: ainda não há restrição por município nem
  permissões mais finas dentro de cada perfil (previstas para uma próxima etapa).
- A distribuição de permissões por perfil é a configuração atual e ainda será validada com o Product
  Owner; pode mudar.
- A recuperação de senha por e-mail e o cadastro por conta própria estão desativados.
- A lista de usuários não mostra o total, apenas as páginas de 20 em 20.
- Os usuários de teste e as senhas padrão servem só para desenvolvimento.
