# 5. Configuração

## Variáveis de ambiente

| Variável | Obrigatória | Default no código | Descrição |
|---|---|---|---|
| `DATABRICKS_WAREHOUSE_ID` | **Sim** | — (app para com erro explícito na inicialização se faltar) | SQL Warehouse usado para todas as queries/DDL do app. |
| `ENVIRONMENT` | Não | `dev` | Rótulo lógico do ambiente (ex.: `dev`/`prd`). Filtra quais schemas de dados aparecem na Governança (por convenção de sufixo `_dev`) e define o sufixo dos schemas internos do app (cadastros e glossário — ver abaixo). |
| `ALLOWED_CATALOGS` | Não | vazio (todos os catálogos visíveis ao usuário) | Allowlist de catálogos de **dados** exibidos no módulo de Governança, separados por vírgula. |
| `USE_ON_BEHALF_OF_USER` | Não | `true` | `true` → leituras e tags da Governança rodam como o usuário logado (OBO); `false` → tudo roda pelo service principal (não recomendado em produção — perde a discriminação por usuário no portão de acesso). |
| `CADASTRO_CATALOG` | Não | `apps` | Catálogo onde ficam **todos** os schemas internos do app: cadastros **e** glossário de termos de negócio. |
| `CADASTRO_SCHEMA` | Não | `governanca_unity_catalog` | **Base** do nome do schema de cadastros (domínios, sub-domínios, stewards, dashboards, padrões de dado pessoal, backlog, permissões, logs). O app monta `CADASTRO_SCHEMA + "_" + ENVIRONMENT` — ex.: `governanca_unity_catalog_prd`. |
| `SEED_ADMIN_EMAIL` | Não | vazio | E-mail promovido a **admin** automaticamente na primeira execução, se a tabela de permissões estiver vazia. Defina antes do primeiro deploy em cada instalação nova — sem isso, ninguém começa como admin. |
| `DATABRICKS_ACCOUNT_ID` | Não | vazio | Habilita a busca de usuários no **nível de conta** (Account SCIM API), além do workspace local. Vazio = busca só no workspace. |
| `DATABRICKS_ACCOUNT_HOST` | Não | `https://accounts.azuredatabricks.net` | Host do console de contas. Só altere fora do Azure. |
| `LLM_ENABLED` | Não | `false` | `true` habilita o painel do **Assistente de Governança (IA)**. Ver [09. Módulo — Assistente de IA](./09-modulo-assistente-ia.md). |
| `LLM_ENDPOINT` | Só se `LLM_ENABLED=true` | vazio | Nome completo (`catalogo.schema.nome_do_modelo`) do model service no Unity Catalog, servido pelo Unity AI Gateway do workspace. Sem isso, o painel do assistente mostra "não configurado" em vez de dar erro. |

> ⚠️ **O schema do glossário de termos de negócio não tem variável própria.**
> Ele é montado no código como `"ontologia_" + ENVIRONMENT` (sempre dentro do
> catálogo de `CADASTRO_CATALOG`) — ex.: `apps.ontologia_prd`. Para usar um
> nome de schema diferente para o glossário, é preciso editar a constante
> `ONTOLOGIA_SCHEMA` em `app.py`, não dá para trocar só pelo `app.yaml`. Ver
> [11. Personalização multi-cliente](./11-personalizacao-multi-cliente.md).

## Constantes no código (não são variáveis de ambiente)

| Constante | Valor | Descrição |
|---|---|---|
| `SAMPLE_ROWS` | `5` | Linhas de amostra exibidas por coluna na Governança. |
| `STATEMENT_TIMEOUT_S` | `120` | Tempo máximo aguardando um statement SQL concluir. |
| `TAG_COMPLIANCE_RULES` | `{"privacidade": "dado pessoal", "seguranca": "confidencial"}` | Valores exigidos nas colunas classificadas como dado pessoal (ver [07. Módulo — Governança de Dados](./07-modulo-governanca-dados.md)). Editar em código para usar outras chaves/valores. |

## `app.yaml` (exemplo completo)

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
  - name: DATABRICKS_ACCOUNT_ID
    value: ""
  - name: LLM_ENABLED
    value: "false"
  - name: LLM_ENDPOINT
    value: ""
```

O `app.yaml` versionado no repositório é a fonte da configuração — ajuste
warehouse, `ENVIRONMENT` e os demais valores conforme o workspace de destino
**antes** de cada deploy (ver [04. Instalação](./04-instalacao.md)).

## Desenvolvimento local (opcional)

```bash
pip install -r requirements.txt
export DATABRICKS_HOST="https://<workspace>.azuredatabricks.net"
export DATABRICKS_TOKEN="<pat-ou-oauth>"
export DATABRICKS_WAREHOUSE_ID="<warehouse-id>"
export ENVIRONMENT="dev"
export USE_ON_BEHALF_OF_USER="false"   # local não tem token OBO; roda como você
streamlit run app.py
```
