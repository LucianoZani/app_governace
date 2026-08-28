# 13. Changelog

Histórico de versões do produto, com base no histórico de commits do
repositório (`git log`).

## v1.5 — 2026-08-28

- **Glossário de Negócio enxuto** — o formulário perdeu a seção de
  Classificação (rótulos de segurança/privacidade) e o campo Observações
  (que passam a ser exclusivos de **Indicador**). "Objetivo" virou
  **Definição** na tela e no card de detalhe (coluna no banco segue `objetivo`).
- **Palavras-chave em "chips"** (Glossário e Indicador) — digita + Enter
  adiciona a palavra abaixo do campo e limpa o campo; `✕` remove. Gravado
  como CSV em `palavras_chave` (compatível com registros antigos).
- **Power Steward no Indicador** — novo primeiro campo do formulário de
  Indicador: dropdown com os usuários marcados com a nova flag `power_steward`
  em Usuários & Permissões (nova checkbox lá; nova coluna `power_steward` em
  `permissoes` e em `indicadores`). Mostra o nome, grava o e-mail. Opcional.
- Tela vazia de Glossário/Indicador mostra "Nenhum termo/indicador cadastrado
  ainda." em vez da tabela com os nomes crus das colunas.

## v1.4 — 2026-08-28

- **Glossário de Negócio e Indicador em telas/tabelas separadas** — a tela
  única "Termos de Negócio (edição)" com seletor de tipo foi dividida em
  duas entradas no grupo **Cadastros**: **Glossário de Negócio** (termos) e
  **Indicador** (KPIs, com os campos próprios e o picker de tabelas de
  dimensão/métrica). Cada uma grava em sua tabela — `ontologia_<env>.
  glossario_negocio` e `ontologia_<env>.indicadores`. A tabela antiga
  `termos_negocio` é migrada por tipo no bootstrap e então removida
  (migração idempotente). A tela de **consulta** (grupo Glossário) continua
  única, mostrando termos e indicadores juntos.

## v1.3 — 2026-08-28

- **Data Owners & Stewards unificados** — Owner e Steward passam a ser o
  mesmo cadastro/tela, com um seletor de tipo. Coluna `tipo` em
  `data_stewards` (migração idempotente; registros antigos → `Steward`).
- **Termos de Negócio: edição × consulta** — a tela de cadastro virou
  "Termos de Negócio (edição)" (grupo Cadastros) e há uma nova tela de
  consulta só-leitura no grupo **Glossário**.
- **Indicadores: formulário condicional + picker de tabelas** — escolher
  "Indicador" revela os campos próprios; dimensão e métrica agora são
  montadas com um seletor Catalog→Schema→Table+colunas (colunas
  `dimensao_tabelas`/`metrica_tabelas` em JSON). Campos legados
  `fonte_variavel`/`tipo_grafico`/`dimensoes` ficam só por compatibilidade.
- **Assistente de IA como painel recolhível à direita** — o painel de chat
  deixou de ser uma coluna fixa que ocupava 1/3 da tela o tempo todo.
  Agora fica ancorado à direita (largura fixa, ~380px), recolhe com o botão
  "→ Recolher" para uma aba "🤖 Assistente" no canto superior direito, e
  reabre ao clicar nela. Estado e histórico persistem na sessão.
  Implementado como `st.container` + CSS `position: fixed` (o Streamlit só
  tem sidebar nativa à esquerda). Conteúdo do chat inalterado.

## v1.2 — 2026-08-21

- **Assistente de Governança (IA)** — painel de chat com function calling
  sobre governança, cadastros, dashboards, padrões de dado pessoal, backlog
  de aprovação, glossário de termos e logs de auditoria. Via Unity AI
  Gateway do próprio workspace; opcional (`LLM_ENABLED`).
- **Glossário de Termos de Negócio** — cadastro de Termos/Indicadores em
  schema dedicado (`ontologia_<env>`), integrado ao Assistente de IA.

## v1.1 — 2026-08-12

- **Compliance de tagueamento de dado pessoal** — colunas classificadas por
  padrão de nome como dado pessoal passam a exigir valores específicos nas
  tags `privacidade`/`seguranca`; tentativas fora da regra vão para um
  **backlog de aprovação** em vez de serem aplicadas direto.
- **Cadastro de Dashboards (AI/BI)** — dashboards vinculados a domínio/
  sub-domínio, visíveis a admin e aos data stewards daquela área.
- Reconhecimento de usuário via header `x-forwarded-email` (SSO do
  Databricks Apps).

## v1.0 — 2026-08-11

- Versão inicial: **Governança de Dados** (tags governadas + comentários em
  tabelas/colunas do Unity Catalog, com os dois modelos de autenticação
  OBO/service principal) e **Cadastros** (Domínios, Sub-domínios, Data
  Stewards, Usuários & Permissões com RBAC, auditoria de comentários/tags).

## Notas sobre este changelog

- As datas seguem os commits do repositório (`https://github.com/LucianoZani/app_governace`),
  não necessariamente a data em que cada instalação de cliente recebeu a
  atualização.
- Este documento cobre o **produto** (código versionado). Mudanças de
  configuração específicas de uma instalação (ex.: nova governed tag
  cadastrada por um cliente) não entram aqui.
