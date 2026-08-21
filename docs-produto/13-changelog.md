# 13. Changelog

Histórico de versões do produto, com base no histórico de commits do
repositório (`git log`).

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
