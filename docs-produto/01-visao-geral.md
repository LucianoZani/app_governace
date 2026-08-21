# 1. Visão geral

## O problema que o produto resolve

Em workspaces Databricks com Unity Catalog, documentar dados (comentários,
tags de classificação) normalmente exige alguém com permissão técnica de
escrita (`MODIFY`) na tabela — o que ou trava a documentação num backlog do
time de dados, ou obriga a dar permissão de escrita a quem só deveria
**documentar**, nunca alterar dados. Some a isso a falta de um lugar único
para registrar domínios de negócio, quem é o dono/steward de cada área, quais
padrões definem um dado como pessoal, e o que cada termo/indicador do negócio
realmente significa — e o conhecimento de governança fica espalhado em
planilhas, threads de chat e na cabeça de poucas pessoas.

## O que o produto é

Um **Databricks App** (Streamlit) com quatro módulos complementares:

| Módulo | O que faz |
|---|---|
| **Governança de Dados** | Usuários de negócio aplicam/editam **tags governadas** e **comentários** em tabelas e colunas do Unity Catalog, com amostra de dados e uma regra de compliance que desvia tentativas de tag incorreta em colunas de dado pessoal para um backlog de aprovação. |
| **Cadastros e Administração** | Domínios, sub-domínios, data stewards, dashboards (AI/BI) vinculados a domínio, padrões de dado pessoal, aprovação do backlog de tags, usuários & permissões (RBAC) e trilha de auditoria. |
| **Assistente de Governança (IA)** | Painel de chat que responde perguntas em linguagem natural sobre tudo o que está cadastrado/governado no app — **só consulta**, nunca aplica tag/comentário/cadastro sozinho. Módulo opcional. |
| **Glossário de Termos de Negócio** | Cadastro rico de termos e indicadores (KPIs) — objetivo, domínio, dono, classificação de segurança/privacidade e, para indicadores, fórmula/memória de cálculo. |

## Para quem é

- **Usuários de negócio / data stewards** — documentam colunas e aplicam
  classificações sem precisar de acesso de escrita aos dados; consultam o
  glossário e o assistente de IA para entender o que já existe.
- **Time de dados / governança** — define o catálogo de tags governadas
  (fora do app, no próprio Unity Catalog), mantém domínios/stewards/padrões de
  dado pessoal, aprova ou rejeita tags que caem no backlog de compliance, e
  audita alterações.
- **Consultorias / times de plataforma** que precisam entregar um painel de
  governança pronto para múltiplos clientes Databricks, sem reescrever o app
  a cada instalação.

## Proposta de valor

1. **Autonomia sem risco** — o usuário de negócio documenta dados sem nunca
   ganhar permissão de escrita sobre eles (ver [Arquitetura](./02-arquitetura.md)).
2. **Governança de dado pessoal automática** — colunas classificadas como
   dado pessoal (por padrão de nome, ex. `cpf`, `email`) só aceitam tags que
   cumprem a regra de compliance definida; o que não cumpre vai para
   aprovação, não é aplicado silenciosamente.
3. **Um só lugar** para domínios, stewards, dashboards, termos de negócio e
   auditoria — em vez de planilhas paralelas.
4. **Consulta em linguagem natural** (opcional) sobre tudo isso, sem expor o
   modelo de IA a nenhuma ação de escrita.
5. **Instalável em qualquer workspace** — nomes de catálogo/schema, warehouse
   e modelo de IA são configuráveis por variável de ambiente (ver
   [Configuração](./05-configuracao.md) e
   [Personalização multi-cliente](./11-personalizacao-multi-cliente.md)).

## O que o app NÃO faz (escopo)

- Não cria/edita as **tags governadas** em si (isso é feito por admins no
  Governance Hub do Unity Catalog — o app só consome esse catálogo).
- Não altera **dados**, apenas metadados (tags e comentários) e os cadastros
  internos do próprio app.
- Não gerencia **permissões de Unity Catalog** (grants) — quem concede acesso
  aos dados continua sendo o time de plataforma/governança, fora do app.
- O Assistente de IA não aplica tag, não grava comentário e não cadastra
  termo — é somente consulta.
