# 8. Referência técnica

## Estrutura de arquivos

```
apps/governanca-unity-catalog/
├── app.py            # aplicação Streamlit (Governança + Cadastros + navegação)
├── app.yaml          # command + env (config do Databricks App)
├── requirements.txt  # dependências
├── .gitignore        # ignora .databricks/ e __pycache__/
├── README.md         # resumo + quickstart
└── docs/             # esta documentação (01…10 + README índice + img/)
```

> O app vive na **raiz do repositório** (`apps/`), fora do Asset Bundle, e é
> deployado **manualmente** (ver [Deploy](./06-deploy.md)).

## Dependências (`requirements.txt`)

- `streamlit>=1.40`, `databricks-sdk>=0.40`, `pandas>=2.0`
- `databricks-sql-connector>=3.0` (opcional; o app usa o SDK/Statement Execution
  API por padrão)

## Walkthrough do `app.py`

### Configuração (topo do arquivo)
- `WAREHOUSE_ID`, `USE_ON_BEHALF_OF_USER`, `ENVIRONMENT`, `ALLOWED_CATALOGS`,
  `SAMPLE_ROWS`, `STATEMENT_TIMEOUT_S` — lidos de env vars/constantes.
- `schema_belongs_to_env(schema)` — regra de filtro dev/prd por sufixo (`_dev`).

### Autenticação
| Função | Responsabilidade |
|---|---|
| `get_service_principal_client()` | `WorkspaceClient()` com a identidade do app (service principal); `@st.cache_resource`. |
| `_forwarded_user_token()` | Recupera o token do usuário do header `x-forwarded-access-token` (`st.context.headers`) quando OBO está ativo. |
| `get_client(prefer_user=False)` | Se `prefer_user=True` **e** OBO habilitado **e** há token → `WorkspaceClient(host, token, auth_type="pat")` (força **só** o token do usuário; evita o erro *"oauth and pat"*). Caso contrário retorna o SP. |
| `current_username()` | Identidade do usuário logado (`get_client(prefer_user=True).current_user.me().user_name`). Em OBO retorna o usuário real; no fallback, o SP. Usado para OBO e para **chavear o cache por usuário**. |

### Helpers de SQL
| Função | Responsabilidade |
|---|---|
| `q_ident(name)` / `q_full(cat,sch,tbl)` | Quoting de identificadores com crase (escapa crases). |
| `q_str(value)` | Quoting de literais string (escapa aspas simples). |
| `run_query(sql, prefer_user)` | Executa via `w.statement_execution.execute_statement` e retorna `DataFrame` (polling até concluir, respeitando `STATEMENT_TIMEOUT_S`). |
| `run_exec(sql, prefer_user)` | `run_query` sem uso do retorno (DDL/DML: `ALTER`/`COMMENT`/`INSERT`…). |
| `_rows_to_df(resp)` | Converte a resposta da Statement Execution API em `DataFrame`. |

> Os metadados do UC passam por **SQL** (não pelas APIs `w.catalogs`/`w.schemas`/
> `w.tables`). As **leituras** rodam com `prefer_user=True` (OBO); as **escritas**
> (`apply_changes`/`apply_table_comment`) rodam com o **SP** (`prefer_user=False`),
> após o portão `user_can_access_table` (OBO).

### Metadados do Unity Catalog (Governança — leituras OBO via SQL, cache por `user`)
| Função | Fonte SQL | Cache |
|---|---|---|
| `list_catalogs(user)` | `SHOW CATALOGS` (filtrado por `ALLOWED_CATALOGS`) | 300s |
| `list_schemas(user, catalog)` | `SHOW SCHEMAS IN <catalog>` | 300s |
| `list_tables(user, catalog, schema)` | `<catalog>.information_schema.tables` | 300s |
| `get_columns(user, …)` | `<catalog>.information_schema.columns` → `ColumnMeta(name, data_type, comment, position)` | 120s |
| `get_applied_column_tags(user, …)` | `<catalog>.information_schema.column_tags` → `{coluna: {tag: valor}}` | 120s |
| `get_column_sample(user, …)` | `SELECT <col> … LIMIT 5` | 60s |
| `get_table_comment(user, …)` | `<catalog>.information_schema.tables.comment` → comentário atual da tabela | 120s |
| `user_can_access_table(user, …)` | `SELECT 1 FROM <catalog>.information_schema.tables …` (OBO) — **portão de acesso** das escritas; fail-closed | 60s |
| `get_governed_tags()` | `w.tag_policies.list_tag_policies()` (**SP**, não OBO) → `{tag_key: [valores]}` | 600s |

### Interface — Governança
| Função | Responsabilidade |
|---|---|
| `render_sidebar()` | Ambiente, usuário, warehouse, autorização, perfil de cadastros e botão "Atualizar dados em tela" (`st.cache_data.clear()`). |
| `select_object(user)` | Seletores encadeados Catalog→Schema→Table (aplica filtro de env e allowlist). |
| `build_columns_dataframe(…)` | Monta a tabela de colunas (aplica busca e o filtro "sem comentário"). |
| `render_table_comment_editor(user, …)` | Bloco de comentário da **tabela** (lê `get_table_comment`, salva via `apply_table_comment`). |
| `render_editor(…)` | Amostra + widgets reativos (comentário / add tag / remover tags) da **coluna**. |
| `apply_changes(user, …)` | Valida `user_can_access_table` (OBO) e executa os statements de coluna (`COMMENT`/`SET TAGS`/`UNSET TAGS`) **via SP**, com feedback por comando e invalidação de caches. |
| `apply_table_comment(user, …)` | Valida `user_can_access_table` (OBO) e executa `COMMENT ON TABLE … IS <texto>\|NULL` **via SP**. |
| `page_governanca()` | Página completa da governança (seleção, listagem, editor, feedback). |

### Cadastros (dados internos do app — executam como **service principal**)
Config: `CAD_CATALOG` (`CADASTRO_CATALOG`, default `apps`),
`CAD_SCHEMA = CADASTRO_SCHEMA + "_" + ENVIRONMENT`, `SEED_ADMIN_EMAIL`.
`_cad(table)` monta o nome totalmente qualificado da tabela de cadastro.

| Função | Responsabilidade |
|---|---|
| `ensure_cadastro_tables()` | `CREATE TABLE IF NOT EXISTS` das 4 tabelas (`dominios`, `subdominios`, `data_stewards`, `permissoes`, todas com `id BIGINT GENERATED ALWAYS AS IDENTITY` + auditoria) e **semeia** o admin (`SEED_ADMIN_EMAIL`) se `permissoes` estiver vazia. Executa como SP; `@st.cache_resource` (roda 1x por processo). |
| `get_role(email)` | Papel nos cadastros (`admin`/`editor`/`leitor`, default `leitor`), lido de `permissoes`. Cache 60s. |
| `can_edit(role)` | `True` para `admin`/`editor`. |
| `list_dominios()` / `list_subdominios()` / `list_stewards()` / `list_permissoes()` | Leituras (SP) com cache curto (30s). |
| `list_workspace_users()` | Usuários do workspace via SCIM (`w.users.list`, **SP**) para a busca de steward/permissão; `[]` se o SP não puder listar (cai para entrada manual). Cache 600s. |
| `_clear_cad_caches()` | Limpa os caches das listagens + `get_role`. |
| `_finish_write(msg)` | Guarda feedback de sucesso, limpa caches e `st.rerun()`. |
| `_show_cad_feedback()` | Exibe o feedback guardado após o rerun. |
| `_count(sql)` | Helper para `SELECT count(*)` (validações de unicidade/vínculo). |

### Páginas de cadastro
| Função | Responsabilidade |
|---|---|
| `page_dominios()` | Lista/CRUD de domínios; valida nome único; **bloqueia exclusão** com sub-domínios/stewards vinculados. |
| `page_subdominios()` | CRUD de sub-domínios (vinculados a um domínio); nome único por domínio; bloqueia exclusão com stewards. |
| `page_stewards()` | Adiciona/remove stewards; **busca de usuário** (pré-preenche nome+e-mail) ou entrada manual; valida vínculo único (domínio+sub+e-mail). |
| `page_permissoes()` | Só admin. Adiciona/edita/exclui papéis; busca de usuário; **impede remover o último admin**. |

> Nas páginas de cadastro, leitura é liberada a todos; **escrita** exige
> `can_edit(role)` — o perfil `leitor` só visualiza.

### Entrada / navegação
| Função | Responsabilidade |
|---|---|
| `main()` | Valida `WAREHOUSE_ID`; resolve `user = current_username()` e `role = get_role(user)`; chama `ensure_cadastro_tables()`; monta `st.navigation({"Cadastros": [...], "Governança": [...]})` — **Cadastros no topo**, *Permissões* só se `role == "admin"`, Governança como página `default=True`. |

## Estratégia de cache

- `@st.cache_resource` para o cliente SP e para `ensure_cadastro_tables`;
  `@st.cache_data(ttl=…)` para listagens e metadados.
- As leituras OBO são **chaveadas por `user`** (o app é um processo compartilhado
  entre vários usuários).
- Após uma escrita de governança, `apply_changes` chama `.clear()` em
  `get_columns`, `get_applied_column_tags` e `get_column_sample`; `apply_table_comment`
  limpa `get_table_comment`. Após uma escrita de cadastro,
  `_finish_write`→`_clear_cad_caches` limpa os caches de cadastro.

## Comandos SQL emitidos (Governança)

```sql
-- comentário
COMMENT ON COLUMN `cat`.`sch`.`tbl`.`col` IS 'texto';   -- ou IS NULL para remover
-- aplicar tag governada (uma coluna por comando)
ALTER TABLE `cat`.`sch`.`tbl` ALTER COLUMN `col` SET TAGS ('chave' = 'valor');
-- remover tag
ALTER TABLE `cat`.`sch`.`tbl` ALTER COLUMN `col` UNSET TAGS ('chave');
```
