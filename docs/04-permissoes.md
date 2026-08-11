# 4. Permissões

O app tem **dois modelos de autenticação** que coexistem:

- **Governança — leituras e TAGS via OBO; só o COMENTÁRIO via SP**
  (`USE_ON_BEHALF_OF_USER=true`). Invariante fixa (ver [Arquitetura](./02-arquitetura.md)):
  - **Leituras** (navegação catálogo/schema/tabela, colunas, amostras,
    comentário/tags atuais) rodam com a **identidade do usuário logado** (via
    SQL/`information_schema`). Cada pessoa só **vê** o que tem permissão no UC.
  - **Tags** (`ALTER TABLE … SET/UNSET TAGS`) rodam **com o token do usuário
    (OBO)**. As tags são governadas pelas permissões do próprio Unity Catalog
    (`APPLY TAG` + `ASSIGN` da governed tag); quem não tiver, simplesmente não
    aplica. **Não passam pelo SP.**
  - **Comentário** (`COMMENT ON TABLE`/`COMMENT ON COLUMN`) é a **única** escrita
    via **service principal do App** (que detém `MODIFY`), porque nenhum usuário
    terá `MODIFY` na tabela — o que liberaria escrita de dados. O usuário só
    precisa **enxergar** a tabela.
  - **Portão de acesso:** antes de gravar o comentário, `user_can_access_table()`
    confirma **com o token do usuário** (OBO) que ele enxerga aquela tabela no
    `information_schema`. Fail-closed: sem acesso, bloqueia.
  - **Auditoria:** cada alteração de comentário e de tag é registrada em
    `apps.governanca_unity_catalog_<env>.log_comentarios` / `log_tags` com o
    **usuário logado** (o UC não guarda o autor do comentário, pois quem executa
    é o SP). Ver [Cadastros](./10-cadastros.md).
  - **Catálogo de tags governadas** (Tag Policies) é lido pelo SP.
  - **Detalhe de implementação:** o cliente OBO é criado com `auth_type="pat"`
    para usar **apenas** o token do usuário; sem isso o SDK também detecta as
    credenciais OAuth do SP e falha com *"more than one authorization method
    configured: oauth and pat"* (ver [Troubleshooting](./09-troubleshooting.md)).
    Se o token do usuário não estiver disponível, as **leituras** caem para o SP
    (e o portão de acesso deixa de discriminar por usuário — mantenha o OBO
    habilitado em produção).
- **Cadastros — service principal do App** (não OBO): os dados de
  domínios/sub/stewards/permissões são gravados/lidos pelo SP. Quem **pode editar**
  é decidido por **RBAC** (tabela `permissoes`, papéis `admin`/`editor`/`leitor`),
  **não** pelas permissões de UC do usuário. Ver [Cadastros](./10-cadastros.md).

Service principal do App — **DEV**: `app-446ya1 governanca-unity-catalog`
(app id `1b65681b-3435-41db-8e25-114b837e9518`); **PROD**:
`app-5o47oi governanca-unity-catalog` (app id
`39aff13e-e4b6-40f5-9bba-8c7027454b29`).

## Habilitar User Authorization / scope `sql` (pré-requisito do OBO)

1. **Admin do workspace habilita** o recurso. Em
   **Settings → Development → Apps**, o setting *"Restrict OAuth scopes for apps"*
   deve permitir o scope `sql` (o valor **All APIs** já permite — que é o caso).
2. **Adicione o scope `sql` ao app.** Pela CLI:
   ```bash
   databricks apps update governanca-unity-catalog \
     --json '{"user_api_scopes":["sql"]}'
   ```
   (ou pela UI: **Edit** → **User authorization** → **+ Add scope** → `sql`; os
   scopes `iam.current-user:read` / `iam.access-control:read` já vêm por padrão).
   A aba **Authorization** da UI é apenas leitura.
3. **Reinicie/redeploy** o app após habilitar. No **primeiro acesso**, cada usuário
   **consente** com os scopes (use uma **janela anônima** se a tela de consentimento
   não aparecer).

## Permissões que cada usuário (steward) precisa — Governança

Para **comentar**, o usuário precisa só de **leitura** (enxergar a tabela) — o
`MODIFY` é emprestado pelo SP. Para **tags**, como rodam via OBO, o usuário
precisa das permissões de tag do **próprio UC**. Conceda a **cada steward**
(idealmente a um **grupo**):

1. **`CAN USE` no SQL Warehouse** do ambiente (as leituras/tags OBO rodam como ele).
2. **Navegação/leitura** nos objetos que ele poderá documentar:
   ```sql
   GRANT USE CATALOG ON CATALOG suprimentos TO `grupo_stewards`;
   GRANT USE SCHEMA, SELECT
     ON SCHEMA suprimentos.estoque_dev TO `grupo_stewards`;   -- por schema do ambiente
   ```
   É esse `SELECT`/`USE` que faz a tabela aparecer no `information_schema` do
   usuário — o **portão de acesso** exige isso para liberar a edição do comentário.
3. **Para aplicar/remover TAGS** (via OBO), o usuário precisa de **`APPLY TAG`**
   no objeto e de **`ASSIGN`** na governed tag:
   ```sql
   GRANT APPLY TAG ON SCHEMA suprimentos.estoque_dev TO `grupo_stewards`;
   ```
   `ASSIGN` da governed tag é concedido **pela UI** (Governance Hub → Governed
   Tags → Account Permissions → Assign), agora **ao usuário/grupo** (não ao SP).
   Sem `APPLY TAG`/`ASSIGN`, a aplicação de tag retorna `PERMISSION_DENIED` — é o
   comportamento esperado (tags seguem a governança do UC). Para **só comentar**,
   `APPLY TAG`/`ASSIGN` não são necessários.

> Observação: o dropdown de tags mostra **todas** as governed tags da conta;
> objetos (catálogo/schema/tabela/coluna) aparecem filtrados pelo acesso do usuário.

## Permissões do service principal do App

Como o SP grava **apenas o comentário**, ele precisa de `MODIFY` (+ leitura) nos
catálogos da allowlist `ALLOWED_CATALOGS`. Hoje a allowlist é
**`suprimentos,rh`** (novos catálogos entram aqui). Ex. no nível do catálogo:
```sql
GRANT USE CATALOG, USE SCHEMA, SELECT, MODIFY ON CATALOG suprimentos TO `<sp-app-id>`;
GRANT USE CATALOG, USE SCHEMA, SELECT, MODIFY ON CATALOG rh          TO `<sp-app-id>`;
```
- `MODIFY` habilita `COMMENT ON TABLE`/`COMMENT ON COLUMN` (a única escrita do SP).
- `SELECT`/`USE` permitem ao SP executar o `COMMENT` e ler o catálogo de tags.
- **`APPLY TAG`/`ASSIGN` NÃO são mais necessários no SP** — as tags rodam via OBO
  (permissão do usuário). Mantê-los no SP é inofensivo, mas não é o caminho usado.

> O poder de escrita do SP é **contido pela allowlist de catálogos** e pelo
> **portão de acesso por usuário**: mesmo com `MODIFY`, o SP só grava o comentário
> quando o usuário logado prova (OBO) que enxerga a tabela.

## Busca de usuários no nível de conta (Account SCIM API)

A busca de usuário dos cadastros (Stewards / Permissões) une **duas fontes**:

1. **Workspace local** — `w.users.list()` (SCIM do workspace, via SP). Sempre
   ativo; requer apenas o SP do App (workspace-level SCIM de leitura é acessível
   ao SP do próprio workspace).
2. **Conta** — `AccountClient.users.list()` (Account SCIM API,
   `/api/2.0/accounts/{account_id}/scim/v2/Users`). **Opcional**: ativa quando
   `DATABRICKS_ACCOUNT_ID` está definido. Encontra usuários que existem na conta
   (Entra ID → identity federation) mas **ainda não foram provisionados no
   workspace local** — o caso do steward que só existe em DEV ao cadastrar em PRD.

**O que o SP precisa para a fonte 2** (Account API):

- O workspace usa **identity federation**, então o SP do App já existe no nível
  de conta — as credenciais OAuth (`DATABRICKS_CLIENT_ID`/`SECRET`, injetadas no
  runtime do App) valem contra `accounts.azuredatabricks.net`; o SDK negocia um
  token novo no host de contas (o token do workspace **não** é reaproveitado).
- Ler usuários pela Account SCIM API exige que o SP tenha papel de **Account
  admin** (não existe papel granular "user reader" na conta). Conceda em
  **Account console → User management → Service principals → `<sp>` → Roles →
  Account admin**. ⚠️ É um privilégio amplo — avalie com o time de plataforma; se
  não for aceitável, deixe `DATABRICKS_ACCOUNT_ID` vazio e use a **entrada
  manual** (abaixo), ou use um **SP dedicado** só para essa leitura, com
  credenciais em secret scope, em vez do SP do App.

**Fallback de entrada manual:** se a busca não encontrar o usuário (ou as duas
fontes falharem), o toggle *"Informar manualmente"* libera digitar **nome +
e-mail corporativo**, salvos direto na tabela Delta de cadastros. Importante:
esse registro é **metadado de negócio** — não valida a existência do usuário em
workspace algum nem concede acesso; quando o usuário for provisionado no
workspace (SCIM sync do Entra ID), o vínculo casa pelo e-mail.

### Visibilidade dos menus (flags por usuário)

Além do papel (`admin`/`editor`/`leitor`), cada usuário tem duas flags na tabela
`permissoes` (editáveis em **Usuários & Permissões**):
- **`ver_cadastros`** — mostra o grupo **Cadastros** (Domínios/Sub/Stewards).
- **`ver_logs`** — mostra o grupo **Auditoria** (Log de comentários / Log de tags).

**Admin enxerga tudo** independentemente das flags. **Governança** é sempre visível.
A tela **Usuários & Permissões** continua **admin-only**.

Para os **Cadastros**, o SP grava no catálogo `apps` e precisa, **no schema do
ambiente** (`apps.governanca_unity_catalog_<env>`):
```sql
GRANT USE CATALOG ON CATALOG apps TO `<sp-app-id>`;
GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY
  ON SCHEMA apps.governanca_unity_catalog_dev TO `<sp-app-id>`;  -- e _prd em PROD
```

## Quem pode abrir o app

Controle em **Apps → governanca-unity-catalog → Permissions** (`CAN_USE` para os
stewards/grupo; `CAN_MANAGE` só para mantenedores). Abrir o app não concede
acesso a dados — isso continua gated pelas permissões de UC de cada um. Quem pode
**editar os cadastros** é uma camada adicional, definida pelo RBAC (ver
[Cadastros](./10-cadastros.md)).
