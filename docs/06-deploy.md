# 6. Deploy

O app é **versionado no repositório** (`apps/governanca-unity-catalog/`) e
**deployado manualmente** pelo Databricks — **não** passa pelo Asset Bundle nem
pelas pipelines de CI/CD.

## Onde subir o código (caminho de source)

Caminho recomendado no workspace:
**`/Workspace/Shared/apps/governanca-unity-catalog`**.

> ⚠️ **Não** use `/Workspace/Shared/env-<target>` (ex.: `/Shared/env-prd`): a
> pipeline de PROD do datalake apaga `/Workspace/Shared/env-prd` **recursivamente**
> a cada deploy (`workspace import-dir … --overwrite`), o que removeria o código do
> app. Mantenha o app **fora** dessas pastas. Uma pasta pessoal também funciona.

## Deploy manual (CLI)

A partir da pasta do app, escolha o ambiente ajustando o `app.yaml` (warehouse +
`ENVIRONMENT`) e rode:

```bash
cd apps/governanca-unity-catalog

# 1) (uma vez) criar o app no workspace
databricks apps create governanca-unity-catalog

# 2) subir o código para o workspace (use --full)
databricks sync . /Workspace/Shared/apps/governanca-unity-catalog --full

# 3) deploy apontando para a pasta sincronizada
databricks apps deploy governanca-unity-catalog \
  --source-code-path /Workspace/Shared/apps/governanca-unity-catalog
```

> O `--source-code-path` pode ser qualquer pasta do workspace onde você subiu o
> código (respeitando o aviso acima). O app é identificado pelo **nome**, então a
> URL não muda entre deploys.

## Habilitar OBO (scope `sql`)

Para a Governança rodar on-behalf-of-user, adicione o scope `sql` ao app e faça
restart/redeploy (ver [Permissões](./04-permissoes.md)):

```bash
databricks apps update governanca-unity-catalog \
  --json '{"user_api_scopes":["sql"]}'
```

No primeiro acesso, cada usuário **consente** com o scope (use janela anônima se a
tela de consentimento não aparecer).

## Deploy manual (UI)

Databricks → **Apps** → **Create app** → aponte o *source code path* para a pasta
onde o código foi sincronizado. Configure command/env pelo `app.yaml` do source.

## Valores por ambiente

O `app.yaml` define as variáveis. Ajuste o warehouse e o `ENVIRONMENT` conforme o
ambiente antes de subir:

| | DEV | PRD |
|---|---|---|
| `DATABRICKS_WAREHOUSE_ID` | `eb0314aa7f7a27c2` | `99d8bd5c236bde9b` |
| `ENVIRONMENT` | `dev` | `prd` |

## Pré-requisitos de permissão

Antes do primeiro uso, garanta (ver [Permissões](./04-permissoes.md)):

- **Usuários (stewards):** só **leitura** — `CAN USE` no SQL Warehouse do ambiente +
  `USE CATALOG`/`USE SCHEMA`/`SELECT` nos schemas alvo. **Sem `MODIFY`/`APPLY TAG`**
  (a escrita é feita pelo SP; o `SELECT` já libera o portão de acesso do app).
- **Service principal do app:** `CAN USE` no warehouse; **`USE SCHEMA`/`SELECT`/
  `MODIFY`/`APPLY TAG`** nos schemas de dados que o app governa (ambiente/allowlist) +
  **`ASSIGN`** nas governed tags (UI → Governance Hub → Account Permissions); e no
  catálogo `apps` — `USE CATALOG` + `USE SCHEMA`/`CREATE TABLE`/`SELECT`/`MODIFY` no
  schema de cadastros do ambiente (`governanca_unity_catalog_<env>`).

> Em **PROD** o app tem um **SP próprio** (distinto do de DEV): repita os grants
> para ele.
