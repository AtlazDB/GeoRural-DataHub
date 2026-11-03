<img width="1074" height="196" alt="Captura de tela 2026-08-30 162135" src="https://github.com/user-attachments/assets/742050b7-d47e-4643-b9cc-ec2421e037af" />


# GeoRural DataHub
<p align="center">
  | <a href="#desafio"> Desafio</a> |
  <a href="#solucao"> Solução</a> |    
  <a href="#backlog"> Backlog do Produto</a> |  
  <a href="#sprint"> Cronograma de Sprints</a> |  
  <a href="#tecnologias">Tecnologias</a> |
  <a href="#estrutura">Estrutura do Projeto</a> |
  <a href="#docs">Documentação</a> |
  <a href="#equipe"> Equipe</a> |
</p>

## 🏅 Desafio <a id="desafio"></a>

A Visiona atua em projetos que utilizam informações territoriais, ambientais e geoespaciais para apoiar processos de análise, planejamento e tomada de decisão. Em diversos cenários, informações relacionadas a imóveis rurais são obtidas de diferentes fontes de dados, públicas ou privadas, contendo registros cadastrais, informações geográficas e indicadores produzidos ao longo do tempo. Atualmente, a diversidade de formatos, níveis de qualidade e atualizações periódicas dificulta a rastreabilidade e a auditabilidade dos dados. Não é simples identificar quais fontes, versões e regras de negócio participaram do cálculo de um indicador específico, gerando desafios de transparência e confiabilidade.

## 🏅 Solução <a id="solucao"></a>

Desenvolvimento do **GeoRural DataHub**, uma plataforma web com **Spring Boot** no back-end e **Vue.js** no front-end. A solução centraliza o controle, validação e governança de dados territoriais em um banco de dados relacional **Oracle**, permitindo a gestão de lotes de ingestão, rastreabilidade completa (*data lineage*) de indicadores ambientais, controle de qualidade de registros rurais através de procedimentos em **PL/SQL** e visualização espacial interativa e reativa com suporte a dados em tempo real via **WebSocket**.

---

## 📋 Backlog do Produto <a id="backlog"></a>

|    ID    | Prioridade | User Story                                                                                                                                                                                                   | Estimativa | Sprint |     Status      |
| :------: | :--------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | :------: | :----: | :-------------: |
| **US01** |    Alta    | Como Operador de Dados, quero importar dados (arquivos) para que os dados recebidos sejam armazenados de forma íntegra e possam ser utilizados pela aplicação.                      |    8    |   1    |  Concluído ✅ |
| **US02** |    Alta    | Como Operador de Dados, quero disponibilizar os dados territoriais do CAR após seu processamento inicial para que as informações dos imóveis rurais possam ser consultadas pela aplicação. |    8     |   1    |  Concluído ✅ |
| **US03** |    Alta    | Como Analista, quero consultar os imóveis rurais por meio de uma API para que suas informações territoriais possam ser consumidas pela aplicação.                   |    5    |   1    |   Concluído ✅ |
| **US04** |    Alta    | Como Analista, quero visualizar os imóveis rurais e suas divisões territoriais em um mapa para que eu possa localizar e analisar espacialmente os imóveis do Paraná.        |    8     |   2    |   Concluído ✅   |
|   US05   |    Alta    | Como Operador de Dados, quero cadastrar fontes informando o link de acesso, o método de requisição e a frequência de coleta, e acompanhar cada execução, para que os dados sejam atualizados automaticamente e eu identifique falhas sem depender do envio manual de arquivos. |    8     |   2    |  Concluído ✅   |
|   US06   |    Alta    | Como Operador de Dados, quero definir como cada coluna da fonte é tratada (destino, tipo de dado e regras de validação) para que somente dados padronizados e válidos cheguem à zona tratada. |    8     |   2    |  Concluído ✅   |
|   US07   |    Alta    | Como Operador de Dados, quero consultar e tratar os registros rejeitados na validação para que eu possa corrigir, aprovar ou descartar cada inconsistência com justificativa registrada. |    5     |   2    |  Concluído ✅   |
|   US08   |    Alta    | Como Analista, quero consultar os indicadores de Reserva Legal e de focos de calor de cada imóvel rural para que eu possa avaliar sua conformidade ambiental. |    5     |   2    |  Concluído ✅   |
|   US09   |   Média    | Como Auditor, quero consultar o histórico das versões dos dados indicadores e sua origem para que eu possa verificar como os resultados foram produzidos e comparar diferentes versões. |    8     |   3    |  Concluído ✅   |
|   US10   |   Média    | Como Gestor, quero controlar o acesso às funcionalidades da aplicação de acordo com o perfil de cada usuário para que informações e operações importantes sejam protegidas contra acessos não autorizados. |    5     |   3    |  Em andamento 🚧  |
|   US11   |   Média    | Como Analista, quero consultar os demais indicadores ambientais de cada imóvel rural (cobertura vegetal, APP, áreas protegidas, embargos e desmatamento) em mapa, gráficos e tabelas para que eu possa avaliar sua situação e seus riscos ambientais. |    8     |   3    |  Em andamento 🚧   |
|   US12   |   Baixa    | Como Analista, quero visualizar os indicadores agregados por município e exportá-los por download e por meio de uma API para que eu possa analisar as regiões e utilizar os resultados em outros sistemas. |    8     |   3    |  Em andamento 🚧   |
|   US13   |   Média    | Como Analista, quero cadastrar novos indicadores ambientais, informando a fonte, o modelo de cálculo e as faixas de status, para que eu possa avaliar os imóveis rurais com novos critérios sem depender de alterações no sistema. |    8     |   3    |  Em andamento 🚧   |


---

## Cronograma das Sprints <a id="sprint"></a>

|    Sprint    | Início |   Fim   | Documentação | Link vídeo | Status | 
| :----------: | :----: | :-----: | :----------: | :----: | :----: |
| 🔖 Sprint 1 | 07/09  | 27/09 |   [Sprint 1](./Documentacao/Processo/Sprints/Sprint1/README.md)             |   [`vídeo disponível`](https://youtu.be/QcErlgeITU0)          |  Concluído ✅    |
| 🔖 Sprint 2 | 05/10  | 25/10 |   [Sprint 2](./Documentacao/Processo/Sprints/Sprint2/README.md)             |   [`vídeo disponível`](https://youtu.be/OA_OV9PeG_U)            |  Concluído ✅    |
| 🔖 Sprint 3 | 02/11  | 22/11 |   [Sprint 3](./Documentacao/Processo/Sprints/Sprint3/README.md)             |   -            |  Em andamento 🚧    |

---
## 💻 Tecnologias <a id="tecnologias"></a>

<h4 align="center">
  <a href="https://www.oracle.com/java/"><img src="https://img.shields.io/badge/Java-ED8B00?style=for-the-badge&logo=openjdk&logoColor=white"/></a>
  <a href="https://spring.io/projects/spring-boot"><img src="https://img.shields.io/badge/SpringBoot-6DB33F?style=for-the-badge&logo=springboot&logoColor=white"/></a>
  <a href="https://www.oracle.com/database/"><img src="https://img.shields.io/badge/Oracle_Spatial-F80000?style=for-the-badge&logo=oracle&logoColor=white"/></a>
  <a href="https://airflow.apache.org/"><img src="https://img.shields.io/badge/Apache_Airflow-017CEE?style=for-the-badge&logo=apacheairflow&logoColor=white"/></a>
  <a href="https://www.oracle.com/cloud/"><img src="https://img.shields.io/badge/Oracle_Cloud-F80000?style=for-the-badge&logo=oracle&logoColor=white"/></a>
  <a href="https://www.keycloak.org/"><img src="https://img.shields.io/badge/Keycloak-4D4D4D?style=for-the-badge&logo=keycloak&logoColor=white"/></a>
  <a href="https://vuejs.org/"><img src="https://img.shields.io/badge/Vue.js-35495E?style=for-the-badge&logo=vue.js&logoColor=4FC08D"/></a>
  <a href="https://leafletjs.com/"><img src="https://img.shields.io/badge/Leaflet-199900?style=for-the-badge&logo=leaflet&logoColor=white"/></a>
  <a href="https://www.chartjs.org/"><img src="https://img.shields.io/badge/Chart.js-FF6384?style=for-the-badge&logo=chartdotjs&logoColor=white"/></a>
  <a href="https://pinia.vuejs.org/"><img src="https://img.shields.io/badge/Pinia-FFE56E?style=for-the-badge&logo=vue.js&logoColor=black"/></a>
  <a href="https://github.com/"><img src="https://img.shields.io/badge/github-%23121011.svg?style=for-the-badge&logo=github&logoColor=white"/></a>
</h4>

## 🏛️ Estrutura do Repositório <a id="estrutura"></a>

- **`backend/`**: API RESTful desenvolvida em Java (Spring Boot) com Spring Data JPA/Hibernate Spatial e Spring Security. Responsável pelas regras de negócio, ingestão de dados geoespaciais, controle de autenticação/autorização, endpoints WebSocket e chamadas a procedures/functions em PL/SQL no banco Oracle.
- **`frontend/`**: Interface web reativa em Vue.js 3 com gerenciamento de estado via Pinia, Axios para requisições assíncronas, Leaflet.js para renderização de mapas GIS e Chart.js para exibição dos indicadores ambientais.
- **`database/`**: Scripts DDL do Oracle Database, incluindo definições de `SDO_GEOMETRY`, triggers, procedures PL/SQL para validação de geometrias e rastreabilidade de dados.
- **`Documentacao/`**: Documentação técnica, especificações de arquitetura e relatórios das Sprints.

## 🚀 Rodando tudo localmente <a id="rodar"></a>

Um comando sobe o ambiente inteiro (Oracle, Airflow, back-end e front-end), no Windows, com o **Docker Desktop** aberto:

```powershell
git submodule update --init   # só na primeira vez: baixa o back e o front
.\build                       # sobe tudo
.\stop                        # desliga tudo (os dados do banco são mantidos)
```

O `.\build` espera o Oracle ficar pronto e abre o back (`http://localhost:8080/api/v1`) e o front (`http://localhost:5173`) em janelas próprias, cada uma com os seus logs. O Airflow (`http://localhost:8088`) só sobe se existir o `infra/airflow/.env.local`; use `.\build -SemAirflow` para pular. Se o back ou o front já estiverem rodando, o script não abre outra cópia.

Antes da primeira vez, siga a configuração de cada parte: o `.env` e a credencial da OCI no README do back-end, e o `.env.local` em `infra/airflow`.

Se você trabalha com o back e o front em outras pastas, ou sobe o back com outro comando, copie `build.local.example.ps1` para `build.local.ps1` e ajuste. Esse arquivo é só da sua máquina e não vai para o git.

## 📂 Documentação <a id="docs"></a>

* 📃 [**Checklist DoR & DoD**](./Documentacao/Processo/Checklist/README.md) - Critérios de início e conclusão de tarefas.
* 🌳 [**Estratégia de Branch & Commits**](./Documentacao/Governanca/README.md) - Fluxo de trabalho no GitHub.
* 📑 [**Manual de Usuário**](./Manual%20de%20usu%C3%A1rio.md) - Manual da aplicação.
* 📑 [**Manual de instalação backend**](https://github.com/AtlazDB/Atlaz4-FrontEnd/edit/dev/README.md) - Rodar backend
* 📑 [**Manual de instalação frontend**](https://github.com/AtlazDB/Atlaz4-BackEnd/edit/main/README.md) - Rodar Frontend

## Equipe

| Foto                                                                     |        Nome       |    Função     |                                                                         Github                                                                        |                                                                                             Linkedin                                                                                              |
| ----------------------------------------------------------------------- | :---------------: | :-----------: | :---------------------------------------------------------------------------------------------------------------------------------------------------: | :-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------: |
| <img src="https://github.com/GabrielRocha-27.png?size=50" width="50">   |   Gabriel Rocha   |   Scrum Master  |  <a href="https://github.com/GabrielRocha-27"><img src="https://img.shields.io/badge/GitHub-100000?style=for-the-badge&logo=github&logoColor=white"></a>  |          <a href="https://www.linkedin.com/in/gabriel-rocha-wk27/"><img src="https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white"></a>           |
| <img src="https://github.com/LeandroHCampos.png?size=50" width="50">    | Leandro Henrique |  Product Owner  |  <a href="https://github.com/LeandroHCampos"><img src="https://img.shields.io/badge/GitHub-100000?style=for-the-badge&logo=github&logoColor=white"></a>   |            <a href="https://www.linkedin.com/in/leandrohcampos/"><img src="https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white"></a>            |
| <img src="https://github.com/gabrielnunes926.png?size=50" width="50">   |   Gabriel Nunes   |   Dev. Team   |  <a href="https://github.com/gabrielnunes926"><img src="https://img.shields.io/badge/GitHub-100000?style=for-the-badge&logo=github&logoColor=white"></a>  | <a href="https://www.linkedin.com/in/gabriel-de-barcelos-nunes-a7a69832a/"><img src="https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white"></a> |
| <img src="https://github.com/gabrielvalentesjc.png?size=50" width="50"> | Gabriel Valente   |   Dev. Team   | <a href="https://github.com/gabrielvalentesjc"><img src="https://img.shields.io/badge/GitHub-100000?style=for-the-badge&logo=github&logoColor=white"></a> |      <a href="https://www.linkedin.com/in/gabriel-valente-b4897226b/"><img src="https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white"></a>       |
| <img src="https://github.com/ManuelaBrito.png?size=50" width="50">   |   Manuela Brito   |   Dev. Team   |  <a href="https://github.com/ManuelaBrito"><img src="https://img.shields.io/badge/GitHub-100000?style=for-the-badge&logo=github&logoColor=white"></a>  | <a href="https://www.linkedin.com/in//"><img src="https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white"></a> |
| <img src="https://github.com/xRod-Rodriguesx.png?size=50" width="50">   |   Rodolfo Corbalan   |   Dev. Team   |  <a href="https://github.com/xRod-Rodriguesx"><img src="https://img.shields.io/badge/GitHub-100000?style=for-the-badge&logo=github&logoColor=white"></a>  | <a href="https://www.linkedin.com/in/rodolfo-corbalan-2a02b4207/"><img src="https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white"></a> |
