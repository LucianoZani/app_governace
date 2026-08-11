# Governança & Cadastros — Unity Catalog (Databricks App)

App Streamlit com duas áreas complementares:

1. **Governança de Dados** — usuários de negócio aplicam/alteram **tags governadas**
   e **comentários** em colunas de tabelas do Unity Catalog, com amostra de dados
   e filtro para encontrar lacunas de documentação.
2. **Cadastros** — Domínios, Sub-domínios, Data Stewards e Permissões (dados
   internos do app, com controle de acesso por papéis).

> 📚 **Documentação completa** por tema (arquitetura, permissões, ambientes,
> deploy, guia de uso, referência técnica, troubleshooting e cadastros) em
> [`docs/`](./docs/README.md).

## Menu (navegação à esquerda)

O app usa `st.navigation`, com dois grupos, **nesta ordem**:

1. **Cadastros** (no topo) → Domínios · Sub-domínios · Data Stewards ·
   Permissões (*Permissões* só aparece para o papel **admin**).
2. **Governança** (abaixo) → **Governança de Dados — Unity Catalog** (página
   inicial/default do app).

Futuras operações do sistema entram abaixo de **Cadastros** também.

## Dois modelos de autenticação (coexistem)

| Área | Executa como | Detalhe |
|---|---|---|
| **Governança** (comentários de tabela/coluna + tags) | **Leitura OBO · escrita SP** | **Leituras** (navegação, colunas, amostras, comentário atual) rodam com a identidade do **usuário logado** (OBO), respeitando os grants dele. **Escritas** (`COMMENT ON`/`ALTER … TAGS`) rodam com o **service principal** (que detém `MODIFY`/`APPLY TAG`), então o usuário **não precisa de `MODIFY`**. Antes de cada escrita, um **portão de acesso** confirma via OBO que o usuário enxerga a tabela — só documenta o que ele tem acesso. |
| **Cadastros** | **Service principal do App** | Dados do app (não do usuário). Quem pode editar é controlado por **RBAC** (tabela `permissoes`: `admin`/`editor`/`leitor`). A página de Governança **não** usa esse RBAC. |

`USE_ON_BEHALF_OF_USER=true`. Se o token do usuário não estiver disponível, o app
cai automaticamente para o service principal (fallback).

## Funcionalidades (Governança)

- Seletores encadeados **Catalog → Schema → Table** (schemas filtrados por ambiente).
- Lista de colunas com **tipo, comentário atual e tags atuais**.
- Filtros (checkboxes) **"Sem comentário"** e **"Sem Tags"** (combináveis) para achar lacunas de documentação.
- **Amostra de até 5 linhas** da coluna selecionada (query em tempo de execução).
- **Somente tags governadas**: as chaves/valores permitidos vêm do catálogo
  oficial de *Governed Tags / Tag Policies* do Unity Catalog.
- Editor por coluna: **comentário** (sempre editável), **adicionar/atualizar tag**
  e **remover tags** (multiselect) — tudo num único **Salvar e Aplicar Governança**,
  que executa `COMMENT ON COLUMN …`, `ALTER TABLE … SET TAGS` / `UNSET TAGS`.

## Arquitetura técnica

- **Interface:** Streamlit (`st.navigation` com as páginas de Cadastros e Governança).
- **Conectividade:** `databricks-sdk`. As queries e DDLs rodam pela
  **Statement Execution API** (`w.statement_execution.execute_statement`) contra um
  **SQL Warehouse**. A navegação e a leitura de colunas usam **SQL**
  (`SHOW CATALOGS`/`SHOW SCHEMAS` e `information_schema`), **não** as APIs
  `w.catalogs`/`w.tables` — assim rodam como o usuário (OBO) e só exigem o scope `sql`.
- **Catálogo de tags governadas:** `w.tag_policies.list_tag_policies()` (lido pelo
  **service principal**) — retorna cada `TagPolicy(tag_key, values=[Value(name=…)])`,
  a lista oficial de chaves e valores permitidos. (O `information_schema.column_tags`
  mostra apenas as tags **já aplicadas**, não o catálogo permitido.)
- **Tags aplicadas:** lidas de `<catalog>.information_schema.column_tags` (OBO).
- **Escritas (`COMMENT ON`/`ALTER … TAGS`):** executadas pelo **service principal**,
  precedidas de um **portão de acesso** (`user_can_access_table`) que confirma via
  OBO que o usuário logado enxerga a tabela no `information_schema`.

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `DATABRICKS_WAREHOUSE_ID` | Sim | SQL Warehouse usado para queries/DDL. Ajuste por ambiente no `app.yaml` ao subir (DEV `` · PRD ``). |
| `ENVIRONMENT` | Não (`dev`) | `dev` mostra/edita só schemas `*_dev`; `prd` só os de PROD. Também define o sufixo do schema de cadastros. Ver "Isolamento de ambiente". |
| `ALLOWED_CATALOGS` | Não | Allowlist de catálogos exibidos na Governança. Neste piloto: `suprimentos`. |
| `USE_ON_BEHALF_OF_USER` | Não (`true`) | `true` = Governança executa com a identidade do usuário logado (OBO). Neste projeto: `true`. |
| `CADASTRO_CATALOG` | Não (`apps`) | Catálogo dos cadastros. |
| `CADASTRO_SCHEMA` | Não (`governanca_unity_catalog`) | **Base** do schema de cadastros; o app acrescenta `_<ENVIRONMENT>`. |
| `SEED_ADMIN_EMAIL` | Não (``) | Admin inicial semeado se a tabela `permissoes` estiver vazia. |
| `DATABRICKS_ACCOUNT_ID` | Não | Habilita a busca de usuários no **nível de conta** (Account SCIM API) — encontra usuários ainda não provisionados no workspace local. Vazio = busca só no workspace. Requer permissão do SP na conta (ver docs/04-permissoes). |
| `DATABRICKS_ACCOUNT_HOST` | Não (`https://accounts.azuredatabricks.net`) | Host do console de contas. Só mude fora do Azure. |

As variáveis ficam no `app.yaml`. O app é subido **manualmente** pelo Databricks
(ver [docs/06-deploy](./docs/06-deploy.md)).

---

## Isolamento de ambiente (metastore unificado)

DEV e PROD compartilham **o mesmo metastore** — os catálogos são os mesmos; a
separação é por **sufixo de schema**:

| Camada | PROD | DEV |
|---|---|---|
| Gold (catálogos de domínio) | `<schema>` (sem sufixo) | `<schema>_dev` |
| Bronze | `*_bronze_prd` | `*_bronze_dev` |
| Silver | `*_silver_prd` | `*_silver_dev` |

O app é **environment-aware** (`ENVIRONMENT`): em `dev` só lista/edita schemas
`*_dev`; em `prd` só os de PROD. A fronteira real, porém, é dada pelos **grants**
(os do usuário, via OBO nas leituras, e os do **SP** nas escritas — limite os
grants do SP aos schemas do ambiente) — o filtro no app é a camada de UX.

**Cadastros também são isolados por ambiente**: o catálogo `apps` é único, mas cada
ambiente usa seu próprio schema — `apps.governanca_unity_catalog_dev` e
`apps.governanca_unity_catalog_prd` (o app concatena `CADASTRO_SCHEMA` + `_ENVIRONMENT`).

## ⚠️ Interação com o job `sync_prod_to_dev` (domingo 20h)

O `notebooks/utils/sync_prod_to_dev.py` espelha **PROD → DEV** e **sobrescreve a
governança (tags/comentários) feita em DEV**. Portanto:

- **Governança escrita em DEV é efêmera**: no próximo sync o espelho re-aplica PROD
  sobre `*_dev`. A documentação "de verdade" deve ser feita em **PROD** — de lá ela
  desce sozinha para DEV. O app de DEV serve para **testar o produto**.
- **O sync NÃO toca o catálogo `apps`**: os **cadastros** (domínios/sub/stewards/
  permissões) **não são afetados** pelo espelhamento.

---

## Permissões (resumo)

Detalhes completos em [docs/04-permissoes](./docs/04-permissoes.md).

- **Governança — usuário (steward):** só **leitura**. `CAN USE` no warehouse do
  ambiente + `USE CATALOG`/`USE SCHEMA`/`SELECT` nos schemas que vai documentar.
  **Sem `MODIFY`/`APPLY TAG`** — o `SELECT` já basta para o portão de acesso liberar
  a edição do comentário/tag (a escrita é feita pelo SP).
- **Service principal do App:** `CAN USE` no warehouse; **`USE SCHEMA`/`SELECT`/
  `MODIFY`/`APPLY TAG`** nos schemas de dados que o app governa (limitados ao
  ambiente/allowlist) + **`ASSIGN`** nas governed tags (só via UI → Governance Hub →
  Governed Tags → Account Permissions); e, no catálogo `apps`, `USE CATALOG` +
  `USE SCHEMA`/`CREATE TABLE`/`SELECT`/`MODIFY` no schema do ambiente (cadastros).

> O dropdown de tags lista **todas** as governed tags (sem filtro por `ASSIGN`);
> aplicar sem permissão retorna `PERMISSION_DENIED`. Os **objetos** aparecem
> filtrados pelo acesso do usuário.

---

## Deploy (manual pelo Databricks)

O app é **versionado no repositório** e subido **manualmente** — **não** passa por
Asset Bundle nem pelas pipelines de CI/CD. Caminho de source recomendado no
workspace: **`/Workspace/Shared/apps/governanca-unity-catalog`** (fora de
`/Shared/env-<target>`, porque a pipeline de PROD apaga `/Workspace/Shared/env-prd`
recursivamente). Ver detalhes em [docs/06-deploy](./docs/06-deploy.md).

```bash
cd apps/governanca-unity-catalog
# ajuste DATABRICKS_WAREHOUSE_ID e ENVIRONMENT no app.yaml conforme o ambiente
databricks apps create governanca-unity-catalog 2>/dev/null || true
databricks sync . /Workspace/Shared/apps/governanca-unity-catalog --full
databricks apps deploy governanca-unity-catalog \
  --source-code-path /Workspace/Shared/apps/governanca-unity-catalog
```

> **Antes de usar em PROD:** conceder ao SP do app (de PROD) o `CAN USE` no warehouse
> `99d8bd5c236bde9b`, os grants nos schemas de produção, o `ASSIGN` nas governed tags
> (UI, Account Permissions) e os grants no schema de cadastros — ver acima.

## Desenvolvimento local (opcional)

```bash
cd apps/governanca-unity-catalog
pip install -r requirements.txt
export DATABRICKS_HOST="https://<workspace>.azuredatabricks.net"
export DATABRICKS_TOKEN="<pat-ou-oauth>"
export DATABRICKS_WAREHOUSE_ID="<warehouse-id>"
export ENVIRONMENT="dev"
export USE_ON_BEHALF_OF_USER="false"   # local não tem token OBO; roda como você
streamlit run app.py
```

## Notas / limitações do Unity Catalog

- **Uma coluna por comando**: o UC não permite `SET TAGS` em várias colunas no
  mesmo `ALTER TABLE`; o app aplica coluna a coluna.
- Chaves de tag são *case-sensitive*; máximo de 50 tags por objeto.
- `SET TAGS` não aceita parâmetros (`?`/`:key`); os valores são quotados com
  escaping seguro no próprio SQL.
- Tags governadas exigem valor da lista permitida da Tag Policy (o app já
  restringe via dropdown quando há valores definidos).
