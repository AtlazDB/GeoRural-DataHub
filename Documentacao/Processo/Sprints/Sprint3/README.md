# Atlaz - Projeto **GeoRural DataHub**

# Documentação - Sprint 3

> Status da Sprint: Em andamento 🚧

---

## 🏅 Desafio

Levar o **GeoRural DataHub** à sua versão final. O acesso passa a ser controlado por perfil de usuário com o **Keycloak**, e o processamento das fontes é refeito no **Apache Airflow** com **GeoPandas**, para suportar as cargas do Paraná inteiro gravando apenas o que mudou. A aplicação passa a exibir os demais indicadores ambientais de cada imóvel e as geometrias usadas no cálculo. O usuário ganha a possibilidade de **cadastrar novos indicadores** sem alteração no sistema, e os resultados passam a ser consolidados por município em um **dashboard**, com exportação por download e por API.

---

| Capacidade estimada da Equipe por Sprint:               | pontos (29)                                          |
| ------------------------------------------------------- | -------------------------------------------- |
| Meta da Sprint:                                         | User Stories de rank 1, 2, 3 e 4 (Total de 29 pontos)    |
| Previsão da Sprint (extras, sem compromisso de entrega) | Nenhuma: a US09, prevista para esta Sprint, foi entregue como extra na Sprint 2           |

| Rank | Prioridade | User Story                                                                                                                                                                                                   | Estimativa | Sprint |
| ---- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------- | ------ |
| 1    | Média      | Como Gestor, quero controlar o acesso às funcionalidades da aplicação de acordo com o perfil de cada usuário para que informações e operações importantes sejam protegidas contra acessos não autorizados. | 5          | 3      |
| 2    | Média      | Como Analista, quero consultar os demais indicadores ambientais de cada imóvel rural (cobertura vegetal, APP, áreas protegidas, embargos e desmatamento) em mapa, gráficos e tabelas para que eu possa avaliar sua situação e seus riscos ambientais. | 8          | 3      |
| 3    | Média      | Como Analista, quero cadastrar novos indicadores ambientais, informando a fonte, o modelo de cálculo e as faixas de status, para que eu possa avaliar os imóveis rurais com novos critérios sem depender de alterações no sistema. | 8          | 3      |
| 4    | Baixa      | Como Analista, quero visualizar os indicadores agregados por município e exportá-los por download e por meio de uma API para que eu possa analisar as regiões e utilizar os resultados em outros sistemas. | 8          | 3      |

---

## 📋 User Stories, DoD & DoR

Nesta seção, detalhamos as histórias de usuário e seus respectivos **DoD (Definition of Done)** e **DoR (Definition of Ready)**, que são os critérios específicos para considerar cada funcionalidade concluída.

* **DoR:** tudo o que precisa existir para **começar** a história.
* **DoD:** tudo o que precisa estar pronto para a história ser considerada **finalizada**.

### **US10 - Controle de acesso por perfil**

> **Como Gestor, quero controlar o acesso às funcionalidades da aplicação de acordo com o perfil de cada usuário para que informações e operações importantes sejam protegidas contra acessos não autorizados.**

- **Prioridade:** Média
    
- **Estimativa:** 5
    
- **Status:** Planejada 📅
    
- **Responsáveis:** Gabriel Rocha (Keycloak) e Gabriel Valente (gerenciamento de usuários)
    
- **DoD (Critérios de Sucesso):**
    
	- Login e logout da aplicação feitos pelo Keycloak
	- Back-end valida o token do usuário em todos os endpoints, respondendo 401 sem login e 403 sem permissão
	- Perfis Operador de Dados, Analista, Auditor e Gestor configurados, cada um com acesso apenas às suas funcionalidades
	- Front-end mostra ao usuário apenas as telas e as ações do seu perfil
	- Tela de gerenciamento de usuários, em que o Gestor dá e tira permissões
	- Toda alteração de permissão registrada com o autor e a data
	- Endpoints documentados no Swagger
 
- **🏃‍ DoR (Definition of Ready):**
    
    - Matriz de perfis por funcionalidade definida
    - Keycloak disponível no ambiente, com o realm e os clientes do back-end e do front-end
    - Protótipo da tela de gerenciamento de usuários
    - Usuários de teste para cada perfil
 
---

### **US11 - Demais indicadores, geometrias e processamento**

> **Como Analista, quero consultar os demais indicadores ambientais de cada imóvel rural (cobertura vegetal, APP, áreas protegidas, embargos e desmatamento) em mapa, gráficos e tabelas para que eu possa avaliar sua situação e seus riscos ambientais.**

- **Prioridade:** Média
    
- **Estimativa:** 8
    
- **Status:** Em andamento 🚧
    
- **Responsáveis:** Leandro Henrique (processamento) e Rodolfo Corbalan (exibição das geometrias dos indicadores)
    
- **DoD (Critérios de Sucesso):**
    
	- Indicadores de cobertura vegetal, APP, áreas protegidas, embargos e desmatamento calculados por imóvel, com a versão dos dados e a regra de cálculo usadas
	- Indicadores consultados em mapa, gráficos e tabelas
	- Geometrias usadas no cálculo dos indicadores exibidas no mapa, carregando apenas a área visível
	- Processamento das fontes feito no Airflow com GeoPandas, sem staging e sem PL/SQL de validação e gravação
	- Carga incremental: registros novos e alterados são gravados, e os iguais não são regravados
	- Coleta por link com páginas pedidas em paralelo, voltando a uma por vez se o servidor de origem falhar
	- Revalidar da quarentena com as mesmas regras da carga, executado pelo Airflow e disparado pelo back-end pela API REST
	- Processamento em Java removido do back-end, com o Airflow como caminho único
	- Tempo de cada etapa do processamento registrado e comparado ao processo anterior, incluindo uma carga completa do CAR do Paraná
	- Endpoints documentados no Swagger

- **🏃‍ DoR (Definition of Ready):**

	- Fontes públicas de cada indicador definidas, com arquivo ou link de exemplo
	- Fórmulas e faixas de status de cada indicador definidas
	- Protótipo da exibição dos indicadores e das geometrias no mapa
	- Tempos do processamento atual medidos, para a comparação

---

### **US13 - Cadastro de novos indicadores**

> **Como Analista, quero cadastrar novos indicadores ambientais, informando a fonte, o modelo de cálculo e as faixas de status, para que eu possa avaliar os imóveis rurais com novos critérios sem depender de alterações no sistema.**

- **Prioridade:** Média
    
- **Estimativa:** 8
    
- **Status:** Em andamento 🚧
    
- **Responsável:** Manuela Brito
    
- **DoD (Critérios de Sucesso):**

	- Fonte nova gravada em uma tabela genérica pelo mapeamento de colunas, sem criar tabela própria, e sem que a carga de uma fonte afete os dados de outra
	- Cadastro de indicador com sigla, nome, unidade, fonte, modelo de cálculo, parâmetros e faixas de status
	- Validações do cadastro: sigla única e no formato definido, fonte com dados carregados, tipo de geometria compatível com o modelo de cálculo e faixas em ordem
	- Indicador pode ser desativado, sem apagar os resultados já calculados
	- Modelos de cálculo por imóvel: densidade de pontos (pontos ÷ área do imóvel × fator) e cobertura de área (área da interseção ÷ área do imóvel × 100), com filtro opcional por categoria
	- Cálculo feito no Airflow, com GeoPandas, o mesmo motor que valida as geometrias
	- Cada resultado guarda o valor, o status, os dois lados da conta, a versão do insumo e a versão do imóvel
	- Consulta dos resultados por imóvel
	- Endpoints documentados no Swagger
	- Fluxo testado do começo ao fim com dados reais, com uma amostra de imóveis conferida por um cálculo independente
	- Memória de cálculo dos dois modelos documentada

- **🏃‍ DoR (Definition of Ready):**
    
	- Modelos de cálculo e formato das faixas de status definidos
	- Tabelas de definição e de resultado dos indicadores modeladas
	- Uma fonte de pontos e uma de polígonos disponíveis para teste
 
---

### **US12 - Dashboard por município e exportação**

> **Como Analista, quero visualizar os indicadores agregados por município e exportá-los por download e por meio de uma API para que eu possa analisar as regiões e utilizar os resultados em outros sistemas.**

- **Prioridade:** Baixa
    
- **Estimativa:** 8
    
- **Status:** Planejada 📅
    
- **Responsável:** Gabriel Nunes
    
- **DoD (Critérios de Sucesso):**

	- Dashboard com os indicadores agregados por município, em gráficos e tabela
	- Filtro por município e por indicador
	- Exportação dos dados exibidos por download
	- Endpoint de consulta dos indicadores agregados por município
	- Cada número exibido permite identificar a versão dos dados que o produziu
	- Endpoints documentados no Swagger

- **🏃‍ DoR (Definition of Ready):**
    
	- Protótipo do dashboard
	- Regra de agregação de cada indicador por município definida
	- Formatos de exportação definidos
	- Indicadores calculados disponíveis (podem ser dados de demonstração)

---

## 🏃‍ DoR - Definition of Ready (Sprint 3)

Estes critérios garantem que o time tem todos os insumos necessários para iniciar o desenvolvimento desta Sprint:

- **Clareza:** User Stories definidas com objetivos de negócio e critérios de aceitação básicos estabelecidos.
    
- **Fontes de dados:** Links das fontes públicas dos demais indicadores e arquivos de exemplo disponíveis.
    
- **Dados de demonstração:** Imóveis, fontes e indicadores de exemplo carregados no banco de desenvolvimento.

- **Regras:** Perfis de acesso, fórmulas dos indicadores, faixas de status e regras de agregação por município definidos.
    
- **Modelagem:** Estrutura do banco definida para os indicadores cadastrados e para a carga incremental.
    
- **Ambiente:** Ambiente de desenvolvimento configurado, com o Oracle, o Airflow e o Keycloak disponíveis.
    
- **Design:** Protótipo das telas de gerenciamento de usuários, de indicadores no mapa e do dashboard definido.


---

## 🏆 Critérios de Conclusão da Sprint

Para o fechamento da Sprint 3, a equipe deve atender aos seguintes requisitos gerais:
* Pull Requests revisados e aprovados seguindo o fluxo do **GitHub Flow**.
* Integração contínua: Código consolidado na branch `main` sem quebras de build.
* Documentação técnica: README da sprint atualizado com o status final das entregas.


---

# BurndownChart da Sprint

> Será incluído ao longo da Sprint.

---
 # 🎥 Demonstração da aplicação

 > Vídeo da demonstração da Sprint: _a produzir ao fim da Sprint_ 🎬

---
