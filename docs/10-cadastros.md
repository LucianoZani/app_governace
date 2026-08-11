# 10. Cadastros (Domínios, Sub-domínios, Data Stewards, Usuários & Permissões) + Auditoria

Além da Governança de Dados (tags/comentários no UC), o app centraliza cadastros
internos. O menu à esquerda (`st.navigation`) tem até três grupos:

1. **Cadastros** → Domínios · Sub-domínios · Data Stewards · Usuários & Permissões
   (admin). Visível para **admin** ou usuário com a flag **`ver_cadastros`**.
2. **Governança** → Governança de Dados — Unity Catalog (OBO, página inicial).
   **Sempre visível.**
3. **Auditoria** → Log de comentários · Log de tags. Visível para **admin** ou
   usuário com a flag **`ver_logs`**.

> **Visibilidade:** admin vê tudo. Para não-admin, `ver_cadastros`/`ver_logs`
> (por usuário, em Usuários & Permissões) liberam os grupos Cadastros/Auditoria.
> O papel (`editor`/`leitor`) define se **edita** ou só **lê** dentro dos Cadastros.

## Modelo de dados

Armazenado no catálogo **`apps`** (isolation **OPEN**; criado com `MANAGED
LOCATION` porque o metastore não tem *storage root* default). **Isolamento por
ambiente:** o catálogo é único (metastore unificado dev+prod), mas cada ambiente
usa seu **próprio schema** — o app monta `CADASTRO_SCHEMA + "_" + ENVIRONMENT`:

- DEV → `apps.governanca_unity_catalog_dev`
- PROD → `apps.governanca_unity_catalog_prd`

Os dados de DEV e PROD são, portanto, **isolados**. Tabelas em cada schema (id
`BIGINT GENERATED ALWAYS AS IDENTITY` + colunas de auditoria
`criado_em`/`criado_por`, `atualizado_em`/`atualizado_por`):

| Tabela | Campos principais | Regras (validadas no app) |
|---|---|---|
| `dominios` | nome, descricao | nome único |
| `subdominios` | dominio_id → domínio, nome, descricao | nome único por domínio |
| `data_stewards` | dominio_id, subdominio_id, nome, email | 1 vínculo por (domínio, sub, e-mail) |
| `permissoes` | email, papel (`admin`/`editor`/`leitor`), `ver_cadastros`, `ver_logs` | e-mail único; ≥ 1 admin sempre |
| `log_comentarios` | usuario, acao, objeto (tabela/coluna), catálogo/schema/tabela/coluna, comentario_anterior/novo, ambiente, criado_em | **append-only** (auditoria) |
| `log_tags` | usuario, acao (aplicar/alterar/remover), catálogo/schema/tabela/coluna, tag_chave, valor_anterior/novo, ambiente, criado_em | **append-only** (auditoria) |

> O UC/Delta **não** força unicidade nem FK — o app valida em código, e os INSERTs
> dos cadastros usam guard atômico `INSERT … WHERE NOT EXISTS`. **Exclusão é
> bloqueada** quando há vínculos: não exclui domínio com sub-domínios/stewards; não
> exclui sub-domínio com stewards; **não remove o último admin** em `permissoes`.

## Auditoria (logs de comentário e tag)

Como o **comentário** é escrito pelo **Service Principal** (o usuário não tem
`MODIFY`), o Unity Catalog não guarda quem alterou. O app resolve isso gravando,
a cada alteração, o **usuário logado (OBO)** em `log_comentarios`/`log_tags` — com
ação, alvo e valores anterior/novo. As **tags** rodam via OBO (o autor já é o
usuário), mas também são logadas para trilha única. As telas **Auditoria › Log de
comentários** e **Log de tags** (admin ou `ver_logs`) mostram os registros com
filtro por usuário e por ação. O registro é **best-effort**: falha de log nunca
bloqueia a governança.

## Como os cadastros gravam (≠ Governança)

- **Service principal do App** grava/lê os cadastros (dados do app, não do
  usuário). Por isso o SP tem, no schema do ambiente, `USE CATALOG`/`USE SCHEMA`/
  `SELECT`/`MODIFY`/`CREATE TABLE`.
- Quem **pode editar** é controlado por **RBAC** (tabela `permissoes`):
  - **admin**: CRUD em tudo + gerencia **Usuários & Permissões** + vê Auditoria;
  - **editor**: CRUD nos cadastros (domínios/sub/stewards);
  - **leitor** (padrão para quem não está em `permissoes`): só visualiza.
  - **flags** `ver_cadastros`/`ver_logs`: liberam a **visualização** dos grupos
    Cadastros/Auditoria para não-admin (admin vê tudo).
- O usuário logado (identidade OBO) é usado nas colunas de auditoria e para
  resolver o papel. Admin inicial semeado: `SEED_ADMIN_EMAIL`
  (`t.guilherme.massafer@ero.com`), inserido se a tabela `permissoes` estiver vazia.

A página de **Governança continua OBO** (permissões do próprio usuário no UC) —
**não** usa este RBAC.

## Data Steward / Permissões — busca de usuário

Os cadastros de steward e de permissão têm um **campo de busca** (por nome ou
e-mail). Os usuários vêm do workspace via SDK/SCIM (SP). Ao selecionar, **Nome** e
**E-mail** são pré-preenchidos. Se o SP não puder listar usuários, cai para entrada
manual (no steward, nome + e-mail; em permissões, e-mail).

## Bootstrap (uma vez por ambiente)

O catálogo/schema precisam existir e o SP precisa dos grants. As **tabelas** o
app cria sozinho (`CREATE TABLE IF NOT EXISTS`, como SP) no primeiro acesso, e
semeia o admin inicial.

```sql
-- catálogo com managed location (metastore sem storage root default), isolation OPEN
CREATE CATALOG IF NOT EXISTS apps
  MANAGED LOCATION 'abfss://unity-catalog-storage@<conta>.dfs.core.windows.net/<workspace_id>/apps';
-- schema do ambiente (ex.: DEV)
CREATE SCHEMA IF NOT EXISTS apps.governanca_unity_catalog_dev;
GRANT USE CATALOG ON CATALOG apps TO `<sp-app-id>`;
GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY
  ON SCHEMA apps.governanca_unity_catalog_dev TO `<sp-app-id>`;
```

> **Metastore unificado:** o catálogo `apps` é único e compartilhado entre DEV e
> PROD (não recriar em PROD). Cada ambiente tem seu **schema** (`…_dev` / `…_prd`)
> e o SP correspondente precisa dos grants nesse schema.
>
> **O job `sync_prod_to_dev` NÃO toca o catálogo `apps`** — os cadastros não são
> espelhados nem sobrescritos pelo sync (ver [Ambientes](./05-ambientes-e-sync.md)).

## Variáveis de ambiente (app.yaml)

| Variável | Default | Descrição |
|---|---|---|
| `CADASTRO_CATALOG` | `apps` | Catálogo dos cadastros. |
| `CADASTRO_SCHEMA` | `governanca_unity_catalog` | **Base** do schema; o app acrescenta `_<ENVIRONMENT>`. |
| `SEED_ADMIN_EMAIL` | `t.guilherme.massafer@ero.com` | Admin inicial (semeado se `permissoes` vazia). |
