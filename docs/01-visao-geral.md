# 1. Visão geral

## Objetivo

Dar autonomia a **usuários de negócio** para documentar e classificar dados no
Unity Catalog — aplicando **tags governadas** e **comentários da tabela e das
colunas** — sem depender de time técnico e sem sair dos padrões de governança da
empresa. Além disso, o app centraliza **cadastros** de governança (domínios,
sub-domínios, data stewards e permissões de acesso ao próprio app).

## Duas áreas (menu à esquerda)

O app usa `st.navigation`, com dois grupos, **nesta ordem**:

1. **Cadastros** (no topo) → Domínios · Sub-domínios · Data Stewards ·
   Permissões (*Permissões* só aparece para o papel **admin**).
2. **Governança** (abaixo) → **Governança de Dados — Unity Catalog** (página
   inicial/default). Futuras operações do sistema entram abaixo de Cadastros.

As duas áreas usam **modelos de autenticação diferentes**: na Governança as
**leituras** rodam **on-behalf-of-user** (identidade de quem está logado) e as
**escritas** rodam pelo **service principal** (com portão de acesso que só
libera tabelas que o usuário enxerga); os Cadastros são gravados pelo **service
principal**, com controle por papéis. Ver [Arquitetura](./02-arquitetura.md) e
[Cadastros](./10-cadastros.md).

## Público-alvo

- **Usuários de negócio / data stewards**: documentam colunas e aplicam
  classificações (ex.: domínio, criticidade, RLS); mantêm os cadastros.
- **Time de dados / governança**: define o catálogo de tags governadas e concede
  as permissões (ver [Permissões](./04-permissoes.md)).

## Funcionalidades — Governança de Dados

1. **Navegação encadeada** Catalog → Schema → Table.
   - O seletor de catálogo pode ser restrito por configuração (`ALLOWED_CATALOGS`);
     no piloto atual, apenas `suprimentos`.
   - Os schemas são filtrados pelo ambiente lógico do app (ver
     [Ambientes](./05-ambientes-e-sync.md)).
2. **Comentário da tabela** — adiciona/edita/remove o comentário da própria
   tabela (`COMMENT ON TABLE`; salvar em branco remove).
3. **Listagem de colunas** com nome, tipo, comentário atual e tags atuais.
4. **Filtros (checkboxes)** "Sem comentário" e "Sem Tags" (combináveis) para achar lacunas de documentação.
5. **Amostra de dados** — até 5 linhas da coluna selecionada, via query em tempo
   de execução, para dar contexto antes de documentar.
6. **Somente tags governadas** — o app lista para seleção apenas as chaves e
   valores permitidos pelo catálogo oficial de *Governed Tags / Tag Policies* do
   Unity Catalog.
7. **Editor por coluna** com três blocos, aplicados num único "Salvar":
   - **Comentário** (sempre editável; salvar em branco remove o comentário);
   - **Adicionar / atualizar tag governada** (chave + valor);
   - **Remover tags** já aplicadas (multiselect).
8. **Portão de acesso** — só é possível documentar tabelas que o **usuário
   logado enxerga** (validado via OBO antes de cada escrita), mesmo com o write
   rodando pelo service principal.
9. **Feedback visual** de sucesso/erro por comando executado.

## Funcionalidades — Cadastros

- **Domínios**, **Sub-domínios** (vinculados a um domínio) e **Data Stewards**
  (vinculados a domínio + sub-domínio, com **busca de usuário** do workspace).
- **Permissões** (só admin): controla quem pode gerenciar os cadastros
  (`admin`/`editor`/`leitor`).
- Exclusões **bloqueadas** quando há vínculos. Ver [Cadastros](./10-cadastros.md).

## O que o app NÃO faz (escopo)

- Não cria/edita **tags governadas** (isso é feito por admins no Governance Hub).
- Não altera dados, apenas **metadados** (tags e comentários) e os **cadastros**
  internos do app.
- Não aplica tags em múltiplas colunas de uma vez (limitação do UC — ver
  [Referência técnica](./08-referencia-tecnica.md)).
- Não gerencia permissões de Unity Catalog (grants).
