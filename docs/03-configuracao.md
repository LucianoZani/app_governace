# 3. Configuração

## Variáveis de ambiente

| Variável | Obrigatória | Default | Descrição |
|---|---|---|---|
| `DATABRICKS_WAREHOUSE_ID` | **Sim** | — | SQL Warehouse usado para queries/DDL. Dinâmico por ambiente. |
| `ENVIRONMENT` | Não | `dev` | `dev` → mostra/edita só schemas `*_dev`; `prd` → só os de PROD. Também define o sufixo do schema de cadastros (`_dev`/`_prd`). |
| `ALLOWED_CATALOGS` | Não | vazio (todos) | Allowlist de catálogos exibidos na Governança (separados por vírgula). Ex.: `suprimentos`. |
| `USE_ON_BEHALF_OF_USER` | Não | `true` | `true` → **leituras** da Governança rodam como o usuário logado (OBO) e alimentam o portão de acesso das escritas (que rodam pelo SP); `false` → leituras caem para o service principal e o portão deixa de discriminar por usuário. Neste projeto: `true`. |
| `CADASTRO_CATALOG` | Não | `apps` | Catálogo dos cadastros (dados internos do app). |
| `CADASTRO_SCHEMA` | Não | `governanca_unity_catalog` | **Base** do schema de cadastros. O app monta `CADASTRO_SCHEMA + "_" + ENVIRONMENT` (ex.: `governanca_unity_catalog_dev`). |
| `SEED_ADMIN_EMAIL` | Não | `t.guilherme.massafer@ero.com` | Admin inicial dos cadastros, semeado se a tabela `permissoes` estiver vazia. |

Constantes no código (`app.py`, não são env vars):

| Constante | Valor | Descrição |
|---|---|---|
| `SAMPLE_ROWS` | `5` | Linhas de amostra exibidas por coluna. |
| `STATEMENT_TIMEOUT_S` | `120` | Tempo máximo aguardando um statement concluir. |

## `app.yaml` (código-fonte)

Define o comando de inicialização e os valores **default** de ambiente. Estado
atual (DEV):

```yaml
command: ["streamlit", "run", "app.py"]
env:
  - name: DATABRICKS_WAREHOUSE_ID
    value: "eb0314aa7f7a27c2"
  - name: USE_ON_BEHALF_OF_USER
    value: "true"
  - name: ENVIRONMENT
    value: "dev"
  - name: ALLOWED_CATALOGS
    value: "suprimentos,rh"
  - name: CADASTRO_CATALOG
    value: "apps"
  - name: CADASTRO_SCHEMA
    value: "governanca_unity_catalog"
  - name: SEED_ADMIN_EMAIL
    value: "t.guilherme.massafer@ero.com"
```

> OBO exige habilitar a **User Authorization** no app + scope `sql` (ver
> [Permissões](./04-permissoes.md)).

## Valores por ambiente (deploy manual)

O app é deployado **manualmente** (ver [Deploy](./06-deploy.md)). O `app.yaml`
versionado é a fonte da configuração; ajuste o warehouse e o `ENVIRONMENT`
conforme o ambiente antes de subir:

| Variável | DEV | PRD |
|---|---|---|
| `DATABRICKS_WAREHOUSE_ID` | `eb0314aa7f7a27c2` | `99d8bd5c236bde9b` |
| `ENVIRONMENT` | `dev` | `prd` |
| `ALLOWED_CATALOGS` | `suprimentos,rh` | `suprimentos,rh` |
| `USE_ON_BEHALF_OF_USER` | `true` | `true` |
| `CADASTRO_CATALOG` | `apps` | `apps` |
| `CADASTRO_SCHEMA` | `governanca_unity_catalog` (→ `_dev`) | `governanca_unity_catalog` (→ `_prd`) |

> O `CADASTRO_SCHEMA` é a **base**: o app acrescenta `_dev`/`_prd` conforme
> `ENVIRONMENT`, isolando os cadastros de cada ambiente no mesmo catálogo `apps`.
