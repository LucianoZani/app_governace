# 4. Instalação (do zero, num workspace novo)

Confirme os [pré-requisitos](./03-pre-requisitos.md) antes de começar.

## Passo 1 — Obter o código

```bash
git clone https://github.com/LucianoZani/app_governace.git governanca-unity-catalog
cd governanca-unity-catalog
```

## Passo 2 — Criar os catálogos/schemas do app

O app precisa de **até dois schemas próprios** (não são os catálogos de dados
de negócio que ele vai governar):

1. **Schema de cadastros** — domínios, sub-domínios, stewards, dashboards,
   padrões de dado pessoal, backlog de aprovação, permissões e logs de
   auditoria. Nome definido por `CADASTRO_CATALOG`/`CADASTRO_SCHEMA` (ver
   [05. Configuração](./05-configuracao.md)).
2. **Schema do glossário de termos de negócio** — usado só pelo módulo
   [Glossário de Termos de Negócio](./10-modulo-glossario-termos.md).

Escolha nomes livres — o app cria as **tabelas** sozinho (`CREATE TABLE IF NOT
EXISTS`) no primeiro acesso; você só precisa garantir que o **catálogo e o
schema** existam e que o service principal tenha os grants (ver
[06. Permissões](./06-permissoes.md)):

```sql
-- catálogo com managed location, se o metastore não tiver storage root default
CREATE CATALOG IF NOT EXISTS apps
  MANAGED LOCATION 'abfss://<container>@<conta>.dfs.core.windows.net/<workspace_id>/apps';

-- schema de cadastros do ambiente
CREATE SCHEMA IF NOT EXISTS apps.governanca_unity_catalog_prd;

-- schema do glossário de termos de negócio
CREATE SCHEMA IF NOT EXISTS apps.ontologia_prd;

GRANT USE CATALOG ON CATALOG apps TO `<service-principal-application-id>`;
GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY
  ON SCHEMA apps.governanca_unity_catalog_prd TO `<service-principal-application-id>`;
GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY
  ON SCHEMA apps.ontologia_prd TO `<service-principal-application-id>`;
```

> Se estiver instalando mais de um ambiente lógico (ex.: dev/prod) no mesmo
> metastore, use um schema por ambiente e a variável `ENVIRONMENT` para
> isolá-los — ver [05. Configuração](./05-configuracao.md).

## Passo 3 — Configurar `app.yaml`

Edite `app.yaml` na raiz do repositório com os valores do workspace de
destino — pelo menos `DATABRICKS_WAREHOUSE_ID` é obrigatório. Veja a lista
completa em [05. Configuração](./05-configuracao.md). Exemplo mínimo:

```yaml
command: ["streamlit", "run", "app.py"]
env:
  - name: DATABRICKS_WAREHOUSE_ID
    value: "<id-do-sql-warehouse>"
  - name: USE_ON_BEHALF_OF_USER
    value: "true"
  - name: ENVIRONMENT
    value: "prd"
  - name: ALLOWED_CATALOGS
    value: "<catalogo1>,<catalogo2>"
  - name: CADASTRO_CATALOG
    value: "apps"
  - name: CADASTRO_SCHEMA
    value: "governanca_unity_catalog"
  - name: SEED_ADMIN_EMAIL
    value: "<seu-email-corporativo>"
```

## Passo 4 — Criar e conceder grants ao service principal

Depois que o Databricks App é criado (próximo passo), ele ganha um service
principal próprio. Conceda a ele os grants descritos em
[06. Permissões](./06-permissoes.md) nos catálogos/schemas de dados que o app
vai governar, além dos schemas do Passo 2.

## Passo 5 — Deploy

Dois caminhos possíveis — escolha conforme o que estiver disponível na
máquina de quem instala.

### Opção A — CLI do Databricks (mais rápido)

```bash
databricks apps create governanca-unity-catalog

databricks sync . /Workspace/Shared/apps/governanca-unity-catalog --full

databricks apps deploy governanca-unity-catalog \
  --source-code-path /Workspace/Shared/apps/governanca-unity-catalog
```

> Evite subir o código dentro de pastas gerenciadas por pipelines de
> CI/CD que fazem `import-dir --overwrite` recursivo (ex.: pastas de
> ambiente tipo `/Shared/env-prd` de um projeto de dados existente) — isso
> pode apagar o código do app no próximo deploy dessas pipelines. Use uma
> pasta dedicada ao app.

### Opção B — Workspace UI manual (sem CLI autenticada)

1. No workspace, vá em **Apps** → **Create app** e aponte o *source code
   path* para uma pasta do Workspace onde você importou os arquivos do
   repositório (`app.py`, `app.yaml`, `requirements.txt`) — use **Import**
   (upload) em cada arquivo se ainda não estiverem lá.
2. Configure o comando de inicialização e as variáveis de ambiente conforme
   o `app.yaml` do Passo 3 (a UI de criação do app lê o `app.yaml` da pasta
   de origem).
3. Depois de criado, toda vez que o código mudar: reimporte os arquivos
   alterados na mesma pasta de origem (a importação **não sobrescreve** um
   arquivo existente — cria um novo com timestamp no nome; mova o antigo
   para a Lixeira e renomeie o novo para o nome original) e clique
   **Implementar** na página do app.

## Passo 6 — Habilitar OBO (scope `sql`)

Para as leituras/tags da Governança rodarem como o usuário logado (em vez de
sempre como o service principal), adicione o scope OAuth `sql` ao app:

```bash
databricks apps update governanca-unity-catalog \
  --json '{"user_api_scopes":["sql"]}'
```

Ou pela UI: **Edit** → **User authorization** → **+ Add scope** → `sql`.
Reinicie/redeploy o app depois de habilitar. No primeiro acesso, cada usuário
precisa **consentir** com o scope — se a tela de consentimento não aparecer,
abra o app numa janela anônima do navegador.

## Passo 7 — (Opcional) Habilitar o Assistente de Governança (IA)

Só depois de confirmar os pré-requisitos de IA (ver
[03. Pré-requisitos](./03-pre-requisitos.md)). Adicione ao `app.yaml`:

```yaml
  - name: LLM_ENABLED
    value: "true"
  - name: LLM_ENDPOINT
    value: "<catalogo>.<schema>.<nome_do_model_service>"
```

Detalhes em [09. Módulo — Assistente de Governança (IA)](./09-modulo-assistente-ia.md).

## Passo 8 — Primeiro acesso

Abra o app (**Apps** → o nome escolhido → **Open**). O app cria sozinho as
tabelas de cadastro e do glossário, e semeia o e-mail de `SEED_ADMIN_EMAIL`
como **admin** inicial em Usuários & Permissões — use essa conta para
liberar os demais usuários.
