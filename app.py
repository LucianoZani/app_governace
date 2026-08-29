"""
App de Governança de Dados — Unity Catalog (Databricks Apps + Streamlit)
=========================================================================

Permite que usuários de negócio apliquem/alterem **tags governadas** e
**comentários** — tanto da **tabela** quanto de suas **colunas** — no Unity
Catalog, com apoio de visualização de amostra de dados e filtros para
encontrar lacunas de documentação.

Princípios de design
--------------------
- **Somente tags governadas**: as chaves e valores permitidos vêm do
  catálogo oficial de *Governed Tags / Tag Policies* do Unity Catalog
  (``w.tag_policies.list_tag_policies()``). O app nunca inventa tags.
- **Leituras = usuário logado (OBO)** — REGRA FIXA: TODO acesso às tabelas do
  catálogo (listagens de catálogo/schema/tabela, colunas, amostras, comentários,
  tags aplicadas) é feito COM O TOKEN DO USUÁRIO. Nunca use o Service Principal
  para ler/navegar o catálogo. Assim o app só mostra o que o usuário enxerga.
- **Tags = usuário logado (OBO)** — REGRA FIXA: aplicar/remover tag governada
  (``ALTER TABLE … SET/UNSET TAGS``) roda COM O TOKEN DO USUÁRIO. As tags são
  governadas pelas permissões do próprio Unity Catalog (``APPLY TAG``/``ASSIGN``);
  quem não tiver a permissão simplesmente não consegue — é o comportamento
  desejado. NÃO use Service Principal para tags.
- **Comentário = Service Principal** — ÚNICA exceção: ``COMMENT ON TABLE`` e
  ``COMMENT ON COLUMN`` rodam com o SP do App (que detém ``MODIFY``), porque
  nenhum usuário terá ``MODIFY`` na tabela (isso liberaria escrita de dados).
  Antes de gravar o comentário, ``user_can_access_table`` confirma via OBO que o
  usuário logado enxerga a tabela — ou seja, ele já tem acesso natural a ela e o
  SP só empresta o ``MODIFY`` para o comentário. É o ÚNICO uso do SP no catálogo.
- **Cadastros e logs internos do app** (schema ``apps.governanca_unity_catalog_*``)
  são gravados pelo SP — não são tabelas do catálogo de negócio, então esta
  invariante não se aplica a eles.
- **Autenticação nativa do Databricks App**: o ``WorkspaceClient()`` usa
  automaticamente as credenciais injetadas no runtime do App.
- **Execução de SQL** via Statement Execution API do ``databricks-sdk``,
  usando um SQL Warehouse (id lido de variável de ambiente).

Veja o README.md para permissões e deploy.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import pandas as pd
import streamlit as st
from databricks.sdk import AccountClient, WorkspaceClient
from databricks.sdk.service.sql import (
    ExecuteStatementRequestOnWaitTimeout,
    StatementState,
)

# ---------------------------------------------------------------------------
# Configuração (via variáveis de ambiente do Databricks App)
# ---------------------------------------------------------------------------

# ID do SQL Warehouse usado para executar as queries e os comandos ALTER.
# No Databricks App, defina em app.yaml (env DATABRICKS_WAREHOUSE_ID) ou
# anexe um recurso "SQL warehouse" ao app.
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip()

# Quando "true", tenta executar as ações usando o token do usuário logado
# (on-behalf-of-user). Requer que a User Authorization esteja habilitada no
# App. Se o token não estiver disponível, cai para o service principal do App.
USE_ON_BEHALF_OF_USER = os.environ.get("USE_ON_BEHALF_OF_USER", "true").lower() == "true"

# Ambiente lógico deste app: "dev" ou "prd". O metastore é UNIFICADO (DEV e PRD
# enxergam os mesmos catálogos); a separação é por sufixo de schema:
#   - Gold  : PROD = schema sem sufixo   | DEV = "<schema>_dev"
#   - Bronze: PROD = "*_bronze_prd"       | DEV = "*_bronze_dev"
#   - Silver: PROD = "*_silver_prd"       | DEV = "*_silver_dev"
# Este app só mostra/edita os schemas do SEU ambiente (fronteira reforçada por
# grants no service principal). Evita que o app de DEV altere tabelas de PROD —
# e vice-versa — num metastore compartilhado.
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev").strip().lower()

# Allowlist de catálogos exibidos no app (separados por vírgula). Vazio = todos
# os catálogos visíveis ao service principal. Ex.: "suprimentos".
ALLOWED_CATALOGS = {
    c.strip().lower() for c in os.environ.get("ALLOWED_CATALOGS", "").split(",") if c.strip()
}

# Busca de usuários no nível de CONTA (Account SCIM API). Permite encontrar
# usuários que existem na conta Databricks mas ainda não foram provisionados
# neste workspace (caso típico: usuário só em DEV ao cadastrar steward em PRD).
# Requer DATABRICKS_ACCOUNT_ID e que o SP do App tenha permissão de leitura de
# usuários na conta (ver docs/04-permissoes.md). Vazio = busca só no workspace.
ACCOUNT_ID = os.environ.get("DATABRICKS_ACCOUNT_ID", "").strip()
ACCOUNT_HOST = os.environ.get(
    "DATABRICKS_ACCOUNT_HOST", "https://accounts.azuredatabricks.net"
).strip()

# Painel "Assistente de Governança" (chat com IA). Usa o Unity AI Gateway
# (model service registrado em UC, não o /serving-endpoints clássico) — o
# nome em LLM_ENDPOINT é o full name catalog.schema.model do model service.
LLM_ENABLED = os.environ.get("LLM_ENABLED", "false").strip().lower() == "true"
LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "").strip()

# Quantidade de linhas de amostra exibidas por coluna.
SAMPLE_ROWS = 5

# Tempo máximo (segundos) aguardando a conclusão de um statement.
STATEMENT_TIMEOUT_S = 120


def schema_belongs_to_env(schema: str) -> bool:
    """True se o schema pertence ao ambiente lógico deste app (ENVIRONMENT).

    Regra por sufixo (metastore unificado): schemas de DEV terminam em ``_dev``
    (cobre ``_bronze_dev``/``_silver_dev``/gold ``<x>_dev``); os demais são de
    PROD. ``information_schema``/``default`` nunca aparecem.
    """
    s = schema.lower()
    if s in ("information_schema", "default"):
        return False
    is_dev_schema = s.endswith("_dev")
    if ENVIRONMENT == "prd":
        return not is_dev_schema
    return is_dev_schema


# ---------------------------------------------------------------------------
# Clientes / autenticação
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def get_service_principal_client() -> WorkspaceClient:
    """Cliente autenticado com a identidade do próprio App (service principal).

    O ``WorkspaceClient()`` sem argumentos usa o *default auth* do Databricks
    SDK, que no runtime de um Databricks App resolve automaticamente host +
    credenciais OAuth do service principal do App.
    """
    return WorkspaceClient()


@st.cache_resource(show_spinner=False)
def get_account_client() -> AccountClient | None:
    """Cliente da Account API autenticado com o service principal do App.

    O runtime do Databricks App injeta ``DATABRICKS_CLIENT_ID``/``SECRET`` do
    SP. Como o workspace usa identity federation, o mesmo SP existe no nível de
    conta e as credenciais OAuth valem contra ``accounts.azuredatabricks.net``
    (o SDK negocia um token novo no host de contas — o token de workspace não é
    reaproveitado). Retorna ``None`` se DATABRICKS_ACCOUNT_ID não estiver
    configurado.
    """
    if not ACCOUNT_ID:
        return None
    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "").strip()
    if not (client_id and client_secret):
        return None
    return AccountClient(
        host=ACCOUNT_HOST,
        account_id=ACCOUNT_ID,
        client_id=client_id,
        client_secret=client_secret,
        auth_type="oauth-m2m",
    )


def _forwarded_user_token() -> str | None:
    """Recupera o token OAuth do usuário logado (on-behalf-of-user).

    O Databricks Apps encaminha o token do usuário no header
    ``x-forwarded-access-token`` quando a User Authorization está habilitada.
    """
    try:
        headers = st.context.headers  # Streamlit >= 1.37
    except Exception:  # pragma: no cover - versões antigas / fora do App
        return None
    if not headers:
        return None
    return headers.get("x-forwarded-access-token")


def _forwarded_user_email() -> str | None:
    """E-mail de quem abriu o app, do header injetado pelo proxy do Databricks Apps.

    Diferente de ``x-forwarded-access-token`` (que só vem com a User
    Authorization/OBO habilitada), o e-mail/username SSO do usuário é
    repassado pelo Apps independentemente de OBO — é só identidade, não
    concede nenhuma permissão extra. Usado para reconhecer quem é admin/editor
    (RBAC dos cadastros) e para os logs de auditoria mesmo com
    ``USE_ON_BEHALF_OF_USER=false``.
    """
    try:
        headers = st.context.headers
    except Exception:
        return None
    if not headers:
        return None
    return headers.get("x-forwarded-email") or headers.get("x-forwarded-preferred-username")


def get_client(prefer_user: bool = False) -> WorkspaceClient:
    """Retorna o WorkspaceClient adequado.

    - ``prefer_user=True`` e OBO habilitado -> usa o token do usuário logado,
      de modo que as permissões (APPLY TAG / ASSIGN) sejam avaliadas contra a
      identidade real de quem está usando o app.
    - Caso contrário, usa o service principal do App.
    """
    if prefer_user and USE_ON_BEHALF_OF_USER:
        token = _forwarded_user_token()
        if token:
            host = os.environ.get("DATABRICKS_HOST") or get_service_principal_client().config.host
            # auth_type="pat" força o uso APENAS do token do usuário. Sem isso, o
            # SDK também detecta as credenciais OAuth do SP injetadas no ambiente
            # (DATABRICKS_CLIENT_ID/SECRET) e falha com
            # "more than one authorization method configured: oauth and pat".
            return WorkspaceClient(host=host, token=token, auth_type="pat")
    return get_service_principal_client()


def get_llm_client():
    """Cliente OpenAI-compatible apontado para o Unity AI Gateway do workspace.

    NÃO cacheado: o token OAuth do service principal expira, então pegamos um
    token fresco (``config.authenticate()``) a cada chamada — mesmo espírito
    de ``get_client()``, que também monta um cliente novo por chamada.
    """
    from openai import OpenAI

    cfg = get_service_principal_client().config
    headers = cfg.authenticate()
    token = (headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if not token:
        raise RuntimeError("Não foi possível obter um token do service principal para o assistente.")
    base_url = f"{cfg.host.rstrip('/')}/ai-gateway/mlflow/v1"
    return OpenAI(api_key=token, base_url=base_url)


# ---------------------------------------------------------------------------
# Helpers de SQL (quoting seguro + execução)
# ---------------------------------------------------------------------------


def q_ident(name: str) -> str:
    """Quota um identificador com crase, escapando crases internas."""
    return "`" + name.replace("`", "``") + "`"


def q_full(catalog: str, schema: str, table: str) -> str:
    """Nome totalmente qualificado e quotado da tabela."""
    return f"{q_ident(catalog)}.{q_ident(schema)}.{q_ident(table)}"


def q_str(value: str) -> str:
    """Quota um literal string, escapando aspas simples."""
    return "'" + value.replace("'", "''") + "'"


def _rows_to_df(resp) -> pd.DataFrame:
    """Converte a resposta da Statement Execution API em DataFrame."""
    if resp.manifest is None or resp.manifest.schema is None:
        return pd.DataFrame()
    cols = [c.name for c in resp.manifest.schema.columns]
    data = []
    if resp.result is not None and resp.result.data_array is not None:
        data = resp.result.data_array
    return pd.DataFrame(data, columns=cols)


def run_query(sql: str, prefer_user: bool = False) -> pd.DataFrame:
    """Executa uma query SQL e retorna um DataFrame (resultados inline)."""
    w = get_client(prefer_user=prefer_user)
    resp = w.statement_execution.execute_statement(
        statement=sql,
        warehouse_id=WAREHOUSE_ID,
        wait_timeout="30s",
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
    )
    deadline = time.time() + STATEMENT_TIMEOUT_S
    while resp.status and resp.status.state in (
        StatementState.PENDING,
        StatementState.RUNNING,
    ):
        if time.time() > deadline:
            raise TimeoutError("Tempo excedido aguardando o statement.")
        time.sleep(1.0)
        resp = w.statement_execution.get_statement(resp.statement_id)

    state = resp.status.state if resp.status else None
    if state != StatementState.SUCCEEDED:
        msg = "Estado inesperado do statement."
        if resp.status and resp.status.error:
            msg = resp.status.error.message
        raise RuntimeError(msg)
    return _rows_to_df(resp)


def run_exec(sql: str, prefer_user: bool = False) -> None:
    """Executa um comando SQL sem esperar resultado (DDL: ALTER/COMMENT)."""
    run_query(sql, prefer_user=prefer_user)


# ---------------------------------------------------------------------------
# Metadados do Unity Catalog (navegação e colunas)
# ---------------------------------------------------------------------------


def current_username() -> str:
    """Identidade do usuário logado (RBAC, auditoria e chave de cache por usuário).

    Prioriza o e-mail do header ``x-forwarded-email`` (SSO do Databricks Apps,
    disponível mesmo com ``USE_ON_BEHALF_OF_USER=false``). Sem esse header
    (fora do App), cai para a identidade do client: o usuário real em OBO, ou
    o service principal em fallback.
    """
    email = _forwarded_user_email()
    if email:
        return email
    try:
        return get_client(prefer_user=True).current_user.me().user_name or "unknown"
    except Exception:
        return "unknown"


# As listagens rodam com o token do usuário (OBO) e via SQL — assim respeitam as
# permissões dele e precisam apenas do escopo `sql`. O parâmetro `user` chaveia o
# cache POR USUÁRIO (o app é um processo compartilhado entre vários usuários).
@st.cache_data(ttl=300, show_spinner=False)
def list_catalogs(user: str) -> list[str]:
    df = run_query("SHOW CATALOGS", prefer_user=True)
    names = df.iloc[:, 0].tolist() if not df.empty else []
    if ALLOWED_CATALOGS:
        names = [n for n in names if str(n).lower() in ALLOWED_CATALOGS]
    return sorted(names)


@st.cache_data(ttl=300, show_spinner=False)
def list_schemas(user: str, catalog: str) -> list[str]:
    df = run_query(f"SHOW SCHEMAS IN {q_ident(catalog)}", prefer_user=True)
    return sorted(df.iloc[:, 0].tolist()) if not df.empty else []


@st.cache_data(ttl=300, show_spinner=False)
def list_tables(user: str, catalog: str, schema: str) -> list[str]:
    sql = (
        f"SELECT table_name FROM {q_ident(catalog)}.information_schema.tables "
        f"WHERE table_schema = {q_str(schema)} ORDER BY table_name"
    )
    df = run_query(sql, prefer_user=True)
    return df["table_name"].tolist() if not df.empty else []


@dataclass
class ColumnMeta:
    name: str
    data_type: str
    comment: str
    position: int


@st.cache_data(ttl=120, show_spinner=False)
def get_columns(user: str, catalog: str, schema: str, table: str) -> list[ColumnMeta]:
    """Lê a lista de colunas (nome, tipo, comentário) via information_schema
    com o token do usuário (OBO) — respeita as permissões dele."""
    sql = f"""
        SELECT column_name, full_data_type, comment, ordinal_position
        FROM {q_ident(catalog)}.information_schema.columns
        WHERE table_schema = {q_str(schema)} AND table_name = {q_str(table)}
        ORDER BY ordinal_position
    """
    df = run_query(sql, prefer_user=True)
    cols: list[ColumnMeta] = []
    for _, r in df.iterrows():
        pos = r["ordinal_position"]
        cols.append(
            ColumnMeta(
                name=r["column_name"] or "",
                data_type=r["full_data_type"] or "",
                comment=r["comment"] or "",
                position=int(pos) if pos not in (None, "") else 0,
            )
        )
    return cols


@st.cache_data(ttl=120, show_spinner=False)
def get_applied_column_tags(user: str, catalog: str, schema: str, table: str) -> dict[str, dict[str, str]]:
    """Tags atualmente aplicadas em cada coluna, via information_schema.

    Retorna: ``{coluna: {tag_key: tag_value}}``.
    """
    sql = f"""
        SELECT column_name, tag_name, tag_value
        FROM {q_ident(catalog)}.information_schema.column_tags
        WHERE schema_name = {q_str(schema)} AND table_name = {q_str(table)}
    """
    df = run_query(sql, prefer_user=True)
    result: dict[str, dict[str, str]] = {}
    for _, row in df.iterrows():
        col = row["column_name"]
        result.setdefault(col, {})[row["tag_name"]] = row["tag_value"]
    return result


@st.cache_data(ttl=60, show_spinner=False)
def get_column_sample(user: str, catalog: str, schema: str, table: str, column: str) -> pd.DataFrame:
    """Amostra de até ``SAMPLE_ROWS`` valores de uma coluna."""
    sql = (
        f"SELECT {q_ident(column)} "
        f"FROM {q_full(catalog, schema, table)} "
        f"LIMIT {SAMPLE_ROWS}"
    )
    return run_query(sql, prefer_user=True)


@st.cache_data(ttl=120, show_spinner=False)
def get_table_comment(user: str, catalog: str, schema: str, table: str) -> str:
    """Comentário atual da própria tabela, via information_schema (OBO)."""
    sql = f"""
        SELECT comment
        FROM {q_ident(catalog)}.information_schema.tables
        WHERE table_schema = {q_str(schema)} AND table_name = {q_str(table)}
        LIMIT 1
    """
    df = run_query(sql, prefer_user=True)
    if df.empty:
        return ""
    return df.iloc[0, 0] or ""


@st.cache_data(ttl=60, show_spinner=False)
def user_can_access_table(user: str, catalog: str, schema: str, table: str) -> bool:
    """Portão de acesso: True se o USUÁRIO logado enxerga a tabela (OBO).

    As escritas rodam com o Service Principal do app (que tem MODIFY / APPLY
    TAG), então o SP conseguiria alterar qualquer tabela. Para garantir o
    requisito "somente as tabelas que o usuário tem acesso", toda escrita é
    precedida por esta verificação feita COM O TOKEN DO USUÁRIO: se a tabela
    aparece no ``information_schema`` dele (o que exige ao menos um privilégio
    de leitura/USE herdado), o usuário tem acesso e a edição é permitida.
    Fail-closed: qualquer erro ou ausência de linha nega a operação.
    """
    sql = f"""
        SELECT 1
        FROM {q_ident(catalog)}.information_schema.tables
        WHERE table_schema = {q_str(schema)} AND table_name = {q_str(table)}
        LIMIT 1
    """
    try:
        df = run_query(sql, prefer_user=True)
        return not df.empty
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Catálogo de tags governadas (Governed Tags / Tag Policies)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=600, show_spinner=False)
def get_governed_tags() -> dict[str, list[str]]:
    """Lista o catálogo oficial de tags governadas e seus valores permitidos.

    Retorna ``{tag_key: [valores_permitidos]}``. Uma lista vazia de valores
    significa que a policy não restringe valores (texto livre permitido).
    """
    w = get_client()
    tags: dict[str, list[str]] = {}
    for policy in w.tag_policies.list_tag_policies():
        key = policy.tag_key
        if not key:
            continue
        values = [v.name for v in (policy.values or []) if v.name]
        tags[key] = sorted(values)
    return dict(sorted(tags.items()))


# ---------------------------------------------------------------------------
# Interface Streamlit
# ---------------------------------------------------------------------------


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### ℹ️ Sessão")
        env_badge = "🟢 PRD" if ENVIRONMENT == "prd" else "🟡 DEV"
        st.caption(f"Ambiente: **{env_badge}** (mostra apenas schemas de {ENVIRONMENT.upper()})")
        user_display = st.session_state.get("user")
        if user_display and user_display != "unknown":
            st.caption(f"Usuário: **{user_display}**")
        else:
            st.caption("Usuário: (não identificado)")
        st.caption(f"Warehouse: `{WAREHOUSE_ID or '—'}`")
        st.caption(
            "Autorização: leituras "
            + ("on-behalf-of-user" if USE_ON_BEHALF_OF_USER else "service principal")
            + " · escritas via service principal (com portão de acesso do usuário)"
        )
        role = st.session_state.get("role")
        if role:
            registrado = bool((st.session_state.get("perms") or {}).get("registrado", True))
            st.caption(f"Perfil (cadastros): **{role if registrado else 'visitante (não cadastrado)'}**")
        st.divider()
        if st.button("🔄 Atualizar dados em tela", use_container_width=True):
            st.cache_data.clear()
            st.rerun()


def select_object(user: str) -> tuple[str | None, str | None, str | None]:
    """Seletores encadeados de Catalog → Schema → Table (visíveis ao usuário)."""
    c1, c2, c3 = st.columns(3)

    with c1:
        catalogs = list_catalogs(user)
        catalog = st.selectbox("Catalog", options=catalogs, index=None, placeholder="Selecione…")

    schema = None
    with c2:
        if catalog:
            # Filtra pelos schemas do ambiente lógico deste app (dev/prd).
            schemas = [s for s in list_schemas(user, catalog) if schema_belongs_to_env(s)]
            schema = st.selectbox(
                "Schema",
                options=schemas,
                index=None,
                placeholder="Selecione…",
                help=f"Exibindo apenas schemas de **{ENVIRONMENT.upper()}**.",
            )

    table = None
    with c3:
        if catalog and schema:
            tables = list_tables(user, catalog, schema)
            table = st.selectbox("Table", options=tables, index=None, placeholder="Selecione…")

    return catalog, schema, table


def build_columns_dataframe(
    columns: list[ColumnMeta],
    applied_tags: dict[str, dict[str, str]],
    only_missing_comment: bool,
    search: str = "",
    only_missing_tags: bool = False,
) -> pd.DataFrame:
    term = search.strip().lower()
    rows = []
    for c in columns:
        tags = applied_tags.get(c.name, {})
        if only_missing_comment and c.comment.strip():
            continue
        if only_missing_tags and tags:
            continue
        # Busca por nome, comentário ou tag da coluna.
        if term:
            haystack = " ".join(
                [c.name, c.comment, " ".join(f"{k} {v}" for k, v in tags.items())]
            ).lower()
            if term not in haystack:
                continue
        tags_str = "; ".join(f"{k}={v}" for k, v in tags.items())
        rows.append(
            {
                "Coluna": c.name,
                "Tipo": c.data_type,
                "Comentário": c.comment,
                "Tags": tags_str,
            }
        )
    return pd.DataFrame(rows, columns=["Coluna", "Tipo", "Comentário", "Tags"])


def render_editor(
    user: str,
    catalog: str,
    schema: str,
    table: str,
    columns: list[ColumnMeta],
    applied_tags: dict[str, dict[str, str]],
    governed_tags: dict[str, list[str]],
    visible_column_names: list[str],
) -> None:
    st.markdown("### ✏️ Editar coluna")

    if not visible_column_names:
        st.info("Nenhuma coluna para editar com o filtro atual.")
        return

    col_name = st.selectbox("Coluna", options=visible_column_names)
    col_meta = next((c for c in columns if c.name == col_name), None)
    if col_meta is None:
        return

    current_tags = applied_tags.get(col_name, {})

    left, right = st.columns([1, 1])

    # ---- Amostra de dados (contexto para o usuário de negócio) ----
    with left:
        st.markdown("**Amostra de dados**")
        st.caption(f"Tipo: `{col_meta.data_type}`")
        try:
            sample = get_column_sample(user, catalog, schema, table, col_name)
            st.dataframe(sample, use_container_width=True, hide_index=True)
        except Exception as exc:
            st.warning(f"Não foi possível carregar a amostra: {exc}")

        if current_tags:
            st.markdown("**Tags atuais nesta coluna**")
            st.dataframe(
                pd.DataFrame(
                    [{"Tag": k, "Valor": v} for k, v in current_tags.items()]
                ),
                use_container_width=True,
                hide_index=True,
            )

    # ---- Governança (widgets reativos — sem st.form, para o campo de valor
    #      aparecer/atualizar assim que a chave da tag é escolhida) ----
    with right:
        # (1) Comentário — sempre editável.
        st.markdown("**📝 Comentário da coluna**")
        new_comment = st.text_area(
            "Comentário da coluna",
            value=col_meta.comment,
            height=100,
            label_visibility="collapsed",
            help="Deixe em branco e salve para remover o comentário.",
            key=f"cmt_{col_name}",
        )

        st.divider()

        # (2) Adicionar / atualizar uma tag governada — CHAVE + VALOR (ambos).
        st.markdown("**🏷️ Adicionar / atualizar tag governada**")
        key_col, val_col = st.columns(2)

        with key_col:
            tag_options = ["(nenhuma)"] + list(governed_tags.keys())
            tag_key = st.selectbox("Chave da tag", options=tag_options, key=f"tagkey_{col_name}")

        tag_value = None
        with val_col:
            if tag_key == "(nenhuma)":
                st.selectbox("Valor da tag", options=["—"], disabled=True,
                             help="Selecione a chave primeiro.")
            else:
                allowed = governed_tags.get(tag_key, [])
                current_value = current_tags.get(tag_key)
                if allowed:
                    default_idx = allowed.index(current_value) if current_value in allowed else 0
                    tag_value = st.selectbox(
                        "Valor da tag", options=allowed, index=default_idx,
                        key=f"tagval_{col_name}_{tag_key}",
                    )
                else:
                    tag_value = st.text_input(
                        "Valor da tag (texto livre)", value=current_value or "",
                        key=f"tagval_{col_name}_{tag_key}",
                    )
        if tag_key != "(nenhuma)" and current_tags.get(tag_key) is not None:
            st.caption(f"Valor atual desta tag: `{current_tags[tag_key]}` (será substituído).")

        st.divider()

        # (3) Remover tags já aplicadas nesta coluna.
        st.markdown("**🗑️ Remover tags desta coluna**")
        if current_tags:
            remove_keys = st.multiselect(
                "Selecione as tags a remover",
                options=list(current_tags.keys()),
                format_func=lambda k: f"{k} = {current_tags[k]}",
                label_visibility="collapsed",
                key=f"rm_{col_name}",
            )
        else:
            remove_keys = []
            st.caption("Nenhuma tag aplicada nesta coluna.")

        st.divider()
        if st.button(
            "💾 Salvar e Aplicar Governança", type="primary",
            use_container_width=True, key=f"save_{col_name}",
        ):
            apply_changes(
                user,
                catalog,
                schema,
                table,
                col_name,
                original_comment=col_meta.comment,
                new_comment=new_comment,
                add_tag_key=None if tag_key == "(nenhuma)" else tag_key,
                add_tag_value=tag_value,
                remove_keys=remove_keys,
            )


def _comment_action(original: str, new: str) -> str:
    """Classifica a operação de comentário: inserir / alterar / remover."""
    o = (original or "").strip()
    n = (new or "").strip()
    if not o and n:
        return "inserir"
    if o and not n:
        return "remover"
    return "alterar"


def _log_comment_change(
    user: str,
    objeto: str,          # 'tabela' | 'coluna'
    catalog: str,
    schema: str,
    table: str,
    column: str | None,
    original_comment: str,
    new_comment: str,
) -> None:
    """Registra na tabela de auditoria quem alterou um comentário (via OBO).

    Best-effort: falha de log NUNCA bloqueia a governança. O executor real no
    Unity Catalog é o Service Principal; aqui gravamos o ``usuario`` logado.
    """
    try:
        acao = _comment_action(original_comment, new_comment)
        run_exec(
            f"INSERT INTO {_cad('log_comentarios')} "
            f"(usuario, executor, acao, objeto, catalogo, db_schema, tabela, coluna, "
            f"comentario_anterior, comentario_novo, ambiente, criado_em) VALUES ("
            f"{q_str(user)}, 'service_principal', {q_str(acao)}, {q_str(objeto)}, "
            f"{q_str(catalog)}, {q_str(schema)}, {q_str(table)}, "
            f"{q_str(column) if column is not None else 'NULL'}, "
            f"{q_str(original_comment or '')}, {q_str(new_comment or '')}, "
            f"{q_str(ENVIRONMENT)}, current_timestamp())"
        )
        try:
            list_log_comentarios.clear()  # visualizador reflete na hora
        except Exception:
            pass
    except Exception:
        # Não interrompe o fluxo se o log falhar (ex.: tabela ainda não criada).
        pass


def _log_tag_change(
    user: str,
    catalog: str,
    schema: str,
    table: str,
    column: str,
    acao: str,                    # aplicar | alterar | remover
    tag_chave: str,
    valor_anterior: str | None,
    valor_novo: str | None,
) -> None:
    """Registra na auditoria quem aplicou/alterou/removeu uma tag de coluna.

    A tag em si é escrita via OBO (permissões do UC do usuário); aqui só gravamos
    o rastro no schema interno do app (via SP). Best-effort: nunca bloqueia.
    """
    try:
        run_exec(
            f"INSERT INTO {_cad('log_tags')} "
            f"(usuario, executor, acao, catalogo, db_schema, tabela, coluna, "
            f"tag_chave, valor_anterior, valor_novo, ambiente, criado_em) VALUES ("
            f"{q_str(user)}, 'on_behalf_of_user', {q_str(acao)}, "
            f"{q_str(catalog)}, {q_str(schema)}, {q_str(table)}, {q_str(column)}, "
            f"{q_str(tag_chave)}, "
            f"{q_str(valor_anterior) if valor_anterior is not None else 'NULL'}, "
            f"{q_str(valor_novo) if valor_novo is not None else 'NULL'}, "
            f"{q_str(ENVIRONMENT)}, current_timestamp())"
        )
        try:
            list_log_tags.clear()  # visualizador reflete na hora
        except Exception:
            pass
    except Exception:
        pass


# Regra de compliance de tagueamento: coluna classificada como dado pessoal
# (ver is_personal_data_column) precisa ter AMBAS as chaves com esses valores.
# Tentativa de gravar uma dessas chaves com outro valor (ou removê-la) numa
# coluna de dado pessoal não é aplicada direto — vai para o backlog de
# aprovação (tag_backlog) até um aprovador decidir.
TAG_COMPLIANCE_RULES = {
    "privacidade": "dado pessoal",
    "seguranca": "confidencial",
}


def is_personal_data_column(column: str) -> bool:
    """True se o nome da coluna casar com algum padrão cadastrado em

    Cadastros → Padrões de Dado Pessoal (substring, case-insensitive; ex.:
    padrão "cpf" casa com "numero_cpf", "cpf_cliente" etc.).
    """
    try:
        padroes = list_padroes_dado_pessoal()
    except Exception:
        return False
    if padroes.empty:
        return False
    col = (column or "").lower()
    return any(
        str(p).strip().lower() in col
        for p in padroes["padrao"].tolist() if str(p).strip()
    )


def tag_violates_compliance(column: str, tag_key: str, new_value: str | None) -> bool:
    """True se `column` é dado pessoal e essa tag_key/valor não cumpre a regra.

    Só avalia as chaves em TAG_COMPLIANCE_RULES — outras chaves nunca violam.
    Remover a chave (new_value=None) também viola: a coluna ficaria sem o
    valor obrigatório.
    """
    required = TAG_COMPLIANCE_RULES.get((tag_key or "").strip().lower())
    if required is None:
        return False
    if not is_personal_data_column(column):
        return False
    return (new_value or "").strip().lower() != required.lower()


def _queue_tag_backlog(
    user: str, catalog: str, schema: str, table: str, column: str,
    tag_key: str, valor_anterior: str | None, valor_novo: str | None, acao: str,
) -> None:
    """Registra uma tentativa de tag não conforme no backlog de aprovação.

    Best-effort (nunca deve travar a tela) — mesmo padrão dos logs de auditoria.
    """
    try:
        required = TAG_COMPLIANCE_RULES.get(tag_key.strip().lower(), "")
        motivo = (
            f"Coluna classificada como dado pessoal: a chave '{tag_key}' precisa "
            f"do valor '{required}' (regra de compliance de tagueamento)."
        )
        run_exec(
            f"INSERT INTO {_cad('tag_backlog')} "
            f"(catalogo, db_schema, tabela, coluna, tag_chave, valor_anterior, valor_novo, "
            f"acao, motivo, solicitante, status, ambiente, criado_em) VALUES ("
            f"{q_str(catalog)}, {q_str(schema)}, {q_str(table)}, {q_str(column)}, {q_str(tag_key)}, "
            f"{q_str(valor_anterior) if valor_anterior is not None else 'NULL'}, "
            f"{q_str(valor_novo) if valor_novo is not None else 'NULL'}, {q_str(acao)}, "
            f"{q_str(motivo)}, {q_str(user)}, 'pendente', {q_str(ENVIRONMENT)}, current_timestamp())"
        )
        try:
            list_tag_backlog.clear()
        except Exception:
            pass
    except Exception:
        pass


def apply_changes(
    user: str,
    catalog: str,
    schema: str,
    table: str,
    column: str,
    original_comment: str,
    new_comment: str,
    add_tag_key: str | None,
    add_tag_value: str | None,
    remove_keys: list[str],
) -> None:
    """Monta e executa os comandos de governança, com feedback visual.

    Identidade de execução (ver docstring do módulo):
      - Comentário  -> Service Principal (usuário não tem MODIFY).
      - Tags        -> OBO/token do usuário (herdam permissões do UC).
    """
    # Portão de acesso: o comentário roda via SP, então validamos via OBO que o
    # usuário logado enxerga a tabela (as tags já rodam com o token dele).
    if not user_can_access_table(user, catalog, schema, table):
        st.error(
            "Você não tem acesso a esta tabela — alteração bloqueada. "
            "Só é possível documentar tabelas que você mesmo enxerga."
        )
        return

    full = q_full(catalog, schema, table)
    col_q = q_ident(column)

    # Tags atuais da coluna — usado para registrar o valor anterior no log.
    try:
        prev_tags = get_applied_column_tags(user, catalog, schema, table).get(column, {})
    except Exception:
        prev_tags = {}

    # Cada item: (descrição, sql, via_obo, log_cb|None).
    #   via_obo=False -> Service Principal (só comentário)
    #   via_obo=True  -> token do usuário (tags)
    statements: list = []
    # Tentativas de tag que violam a regra de compliance (coluna de dado
    # pessoal sem privacidade=dado pessoal / seguranca=confidencial) vão pra
    # cá em vez de "statements" — não são executadas, ficam pendentes.
    backlog: list[tuple[str, str | None, str | None, str]] = []

    # 1) Comentário (Service Principal) — só altera se mudou.
    if new_comment != original_comment:
        col_ref = f"{full}.{col_q}"
        if new_comment.strip() == "":
            desc, csql = "Remover comentário", f"COMMENT ON COLUMN {col_ref} IS NULL"
        else:
            desc, csql = "Atualizar comentário", f"COMMENT ON COLUMN {col_ref} IS {q_str(new_comment)}"
        statements.append((
            desc, csql, False,
            (lambda oc=original_comment, nc=new_comment: _log_comment_change(
                user, "coluna", catalog, schema, table, column, oc, nc)),
        ))

    # 2) Remover tags selecionadas (OBO) — exceto a que está sendo (re)aplicada.
    for key in remove_keys:
        if key == add_tag_key:
            continue
        if tag_violates_compliance(column, key, None):
            backlog.append((key, prev_tags.get(key), None, "remover"))
            continue
        statements.append((
            f"Remover tag '{key}'",
            f"ALTER TABLE {full} ALTER COLUMN {col_q} UNSET TAGS ({q_str(key)})",
            True,
            (lambda k=key: _log_tag_change(
                user, catalog, schema, table, column, "remover", k, prev_tags.get(k), None)),
        ))

    # 3) Adicionar / atualizar a tag governada (OBO).
    if add_tag_key:
        if add_tag_value is not None and add_tag_value != "":
            acao_tag = "alterar" if add_tag_key in prev_tags else "aplicar"
            if tag_violates_compliance(column, add_tag_key, add_tag_value):
                backlog.append((add_tag_key, prev_tags.get(add_tag_key), add_tag_value, acao_tag))
            else:
                statements.append((
                    f"Aplicar tag '{add_tag_key}' = '{add_tag_value}'",
                    f"ALTER TABLE {full} ALTER COLUMN {col_q} "
                    f"SET TAGS ({q_str(add_tag_key)} = {q_str(add_tag_value)})",
                    True,
                    (lambda k=add_tag_key, v=add_tag_value, a=acao_tag: _log_tag_change(
                        user, catalog, schema, table, column, a, k, prev_tags.get(k), v)),
                ))
        else:
            st.warning(f"Selecione um valor para a tag '{add_tag_key}'.")

    if not statements and not backlog:
        st.info("Nenhuma alteração a aplicar.")
        return

    feedback: list[tuple[str, str]] = []
    ok, fail = 0, 0
    for desc, sql, via_obo, log_cb in statements:
        try:
            # Comentário -> SP; Tags -> OBO (token do usuário). Ver módulo.
            run_exec(sql, prefer_user=via_obo)
            feedback.append(("success", f"✅ {desc}"))
            ok += 1
            if log_cb is not None:
                log_cb()  # auditoria (best-effort dentro do próprio helper)
        except Exception as exc:
            feedback.append(("error", f"❌ {desc} — {exc}"))
            fail += 1

    # Tentativas não conformes (coluna de dado pessoal sem privacidade/segurança
    # corretas): não aplicadas — vão para o backlog de aprovação.
    for tag_key, valor_anterior, valor_novo, acao in backlog:
        _queue_tag_backlog(
            user, catalog, schema, table, column, tag_key, valor_anterior, valor_novo, acao,
        )
        required = TAG_COMPLIANCE_RULES.get(tag_key.strip().lower(), "")
        feedback.append((
            "warning",
            f"⏳ Tag '{tag_key}' foi para aprovação — coluna é dado pessoal e exige "
            f"'{tag_key}' = '{required}' (requer aprovação de um governança aprovador).",
        ))

    # Guarda o feedback para exibir após o rerun (a listagem recarrega já
    # refletindo o novo estado — comentários e tags atualizados na tela).
    st.session_state["save_feedback"] = feedback
    if ok:
        # Invalida os caches de metadados para reler o estado atualizado.
        get_columns.clear()
        get_applied_column_tags.clear()
        get_column_sample.clear()
    st.rerun()


def apply_table_comment(
    user: str,
    catalog: str,
    schema: str,
    table: str,
    original_comment: str,
    new_comment: str,
) -> None:
    """Adiciona/edita/remove o comentário da PRÓPRIA tabela (COMMENT ON TABLE).

    Escreve via Service Principal, mas só depois de confirmar (OBO) que o
    usuário logado tem acesso à tabela.
    """
    if new_comment == original_comment:
        st.info("Nenhuma alteração no comentário da tabela.")
        return

    if not user_can_access_table(user, catalog, schema, table):
        st.error(
            "Você não tem acesso a esta tabela — alteração bloqueada. "
            "Só é possível documentar tabelas que você mesmo enxerga."
        )
        return

    full = q_full(catalog, schema, table)
    if new_comment.strip() == "":
        desc = "Remover comentário da tabela"
        sql = f"COMMENT ON TABLE {full} IS NULL"
    else:
        desc = "Atualizar comentário da tabela"
        sql = f"COMMENT ON TABLE {full} IS {q_str(new_comment)}"

    try:
        run_exec(sql)  # SP (acesso do usuário já validado acima)
        st.session_state["save_feedback"] = [("success", f"✅ {desc}")]
        get_table_comment.clear()
        # Auditoria: registra quem (usuário logado) alterou o comentário.
        _log_comment_change(
            user, "tabela", catalog, schema, table, None,
            original_comment, new_comment,
        )
    except Exception as exc:
        st.session_state["save_feedback"] = [("error", f"❌ {desc} — {exc}")]
    st.rerun()


def render_table_comment_editor(user: str, catalog: str, schema: str, table: str) -> None:
    """Seção para adicionar/editar/remover o comentário da tabela."""
    st.markdown("### 📝 Comentário da tabela")
    try:
        current = get_table_comment(user, catalog, schema, table)
    except Exception as exc:
        st.warning(f"Não foi possível ler o comentário atual da tabela: {exc}")
        current = ""

    key_base = f"{catalog}.{schema}.{table}"
    new_comment = st.text_area(
        "Comentário da tabela",
        value=current,
        height=90,
        label_visibility="collapsed",
        help="Descreva o conteúdo/propósito da tabela. Deixe em branco e salve para remover o comentário.",
        key=f"tblcmt_{key_base}",
    )
    if st.button(
        "💾 Salvar comentário da tabela", type="primary",
        use_container_width=False, key=f"savetbl_{key_base}",
    ):
        apply_table_comment(user, catalog, schema, table, current, new_comment)


def page_governanca() -> None:
    """Página: Governança de Dados — Unity Catalog (tags governadas + comentários)."""
    st.title("🏷️ Governança de Dados — Unity Catalog")
    st.caption(
        "Aplique e altere **comentários da tabela e das colunas** e **tags "
        "governadas** no Unity Catalog. Você só edita tabelas às quais tem acesso."
    )

    user = st.session_state.get("user") or current_username()

    # Feedback do último "Salvar" (exibido após o rerun que recarrega a listagem).
    _feedback = st.session_state.pop("save_feedback", None)
    if _feedback:
        _kind_fn = {"success": st.success, "warning": st.warning, "error": st.error}
        for _kind, _msg in _feedback:
            _kind_fn.get(_kind, st.error)(_msg)

    catalog, schema, table = select_object(user)
    if not (catalog and schema and table):
        st.info("Selecione Catalog, Schema e Table para começar.")
        return

    try:
        governed_tags = get_governed_tags()
    except Exception as exc:
        governed_tags = {}
        st.warning(
            "Não foi possível carregar o catálogo de tags governadas "
            f"(Tag Policies): {exc}"
        )

    if not governed_tags:
        st.warning(
            "Nenhuma **tag governada** encontrada na conta. Só é possível "
            "editar comentários. Peça a um admin para criar Governed Tags."
        )

    try:
        columns = get_columns(user, catalog, schema, table)
        applied_tags = get_applied_column_tags(user, catalog, schema, table)
    except Exception as exc:
        st.error(f"Falha ao carregar metadados da tabela: {exc}")
        return

    st.divider()
    render_table_comment_editor(user, catalog, schema, table)

    st.divider()
    st.markdown(f"### 📋 Colunas de `{catalog}.{schema}.{table}`")

    c_search, c_cb1, c_cb2 = st.columns([3, 1, 1])
    with c_search:
        search = st.text_input(
            "🔍 Buscar coluna",
            value="",
            placeholder="Filtre por nome da coluna, comentário ou tag…",
            label_visibility="collapsed",
        )
    with c_cb1:
        sem_comentario = st.checkbox(
            "Sem comentário", value=False,
            help="Mostra só colunas sem comentário (lacunas de documentação).",
        )
    with c_cb2:
        sem_tags = st.checkbox(
            "Sem Tags", value=False,
            help="Mostra só colunas sem nenhuma tag aplicada.",
        )

    df = build_columns_dataframe(columns, applied_tags, sem_comentario, search, sem_tags)
    st.caption(f"{len(df)} coluna(s) exibida(s).")
    st.dataframe(df, use_container_width=True, hide_index=True)

    visible_names = df["Coluna"].tolist()

    st.divider()
    render_editor(
        user,
        catalog,
        schema,
        table,
        columns,
        applied_tags,
        governed_tags,
        visible_names,
    )


# ===========================================================================
# CADASTROS (dados internos do app em apps.governanca_unity_catalog)
# ---------------------------------------------------------------------------
# Gravados/lidos pelo SERVICE PRINCIPAL (app authorization) — são dados do app,
# não do usuário. O controle de quem pode editar é feito por RBAC (tabela
# `permissoes`, papéis admin/editor/leitor). A página de Governança (tags UC)
# NÃO usa este RBAC — ela é OBO.
# ===========================================================================

CAD_CATALOG = os.environ.get("CADASTRO_CATALOG", "apps").strip()
# Isolamento por ambiente: o catálogo `apps` é único (metastore unificado), mas
# cada ambiente usa seu PRÓPRIO schema (…_dev / …_prd). CADASTRO_SCHEMA é a base;
# o app acrescenta o sufixo do ENVIRONMENT.
_CAD_SCHEMA_BASE = os.environ.get("CADASTRO_SCHEMA", "governanca_unity_catalog").strip()
CAD_SCHEMA = f"{_CAD_SCHEMA_BASE}_{ENVIRONMENT}"
SEED_ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "t.guilherme.massafer@ero.com").strip().lower()

# Glossário de termos de negócio / indicadores — mesmo catálogo `apps`, schema
# próprio (`ontologia_<env>`) pra não misturar com as tabelas de cadastro do
# app. O schema em si é criado manualmente (ver docs/04-permissoes.md); aqui
# só criamos a tabela, igual às demais.
ONTOLOGIA_SCHEMA = f"ontologia_{ENVIRONMENT}"


def _cad(table: str) -> str:
    """Nome totalmente qualificado de uma tabela de cadastro."""
    return f"{q_ident(CAD_CATALOG)}.{q_ident(CAD_SCHEMA)}.{q_ident(table)}"


def _ont(table: str) -> str:
    """Nome totalmente qualificado de uma tabela do glossário/ontologia."""
    return f"{q_ident(CAD_CATALOG)}.{q_ident(ONTOLOGIA_SCHEMA)}.{q_ident(table)}"


@st.cache_resource(show_spinner=False)
def ensure_cadastro_tables() -> bool:
    """Cria as tabelas dos cadastros (idempotente) e semeia o admin inicial.

    Executa como o service principal do app. Cacheado por processo (roda 1x).
    """
    audit = (
        "criado_em TIMESTAMP, criado_por STRING, "
        "atualizado_em TIMESTAMP, atualizado_por STRING"
    )
    ddl = [
        f"CREATE TABLE IF NOT EXISTS {_cad('dominios')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, nome STRING, descricao STRING, {audit})",
        f"CREATE TABLE IF NOT EXISTS {_cad('subdominios')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, dominio_id BIGINT, nome STRING, descricao STRING, {audit})",
        # Cadastro de responsáveis por domínio/sub-domínio. `tipo` distingue
        # Data Owner de Data Steward — mesma tabela, mesmo formulário (com um
        # seletor de tipo no topo), pra reaproveitar toda a lógica de vínculo
        # a domínio/sub-domínio.
        f"CREATE TABLE IF NOT EXISTS {_cad('data_stewards')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, dominio_id BIGINT, subdominio_id BIGINT, "
        f"tipo STRING, nome STRING, email STRING, {audit})",
        f"CREATE TABLE IF NOT EXISTS {_cad('permissoes')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, email STRING, papel STRING, {audit})",
        # Dashboards AI/BI (Lakeview) registrados no app, vinculados a um domínio
        # (e opcionalmente sub-domínio). Quem enxerga cada dashboard no menu é
        # quem for admin ou Data Steward daquele domínio/sub-domínio — reaproveita
        # o mesmo cadastro de stewards em vez de uma lista de acesso paralela.
        f"CREATE TABLE IF NOT EXISTS {_cad('dashboards')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, dominio_id BIGINT, subdominio_id BIGINT, "
        f"nome STRING, descricao STRING, url STRING, icone STRING, ativo BOOLEAN, {audit})",
        # Log de auditoria (append-only) das alterações de COMENTÁRIO. Como a
        # escrita do COMMENT ON roda via Service Principal, o Unity Catalog não
        # guarda o usuário real; aqui registramos o usuário logado (OBO) que de
        # fato solicitou a inserção/alteração/remoção do comentário.
        f"CREATE TABLE IF NOT EXISTS {_cad('log_comentarios')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, "
        f"usuario STRING, executor STRING, acao STRING, objeto STRING, "
        f"catalogo STRING, db_schema STRING, tabela STRING, coluna STRING, "
        f"comentario_anterior STRING, comentario_novo STRING, "
        f"ambiente STRING, criado_em TIMESTAMP)",
        # Log de auditoria (append-only) das alterações de TAG governada. Mesma
        # lógica do log de comentários: o SET/UNSET TAGS roda via SP, então
        # registramos aqui o usuário logado (OBO) que solicitou a alteração.
        f"CREATE TABLE IF NOT EXISTS {_cad('log_tags')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, "
        f"usuario STRING, executor STRING, acao STRING, "
        f"catalogo STRING, db_schema STRING, tabela STRING, coluna STRING, "
        f"tag_chave STRING, valor_anterior STRING, valor_novo STRING, "
        f"ambiente STRING, criado_em TIMESTAMP)",
        # Padrões (substring, case-insensitive) de nome de coluna que classificam
        # um dado como pessoal (cpf, nome, email, ...). Mantido pela governança —
        # dispara a regra de compliance de tagueamento em apply_changes().
        f"CREATE TABLE IF NOT EXISTS {_cad('padroes_dado_pessoal')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, padrao STRING, descricao STRING, {audit})",
        # Backlog de aprovação: tentativas de tag em coluna de dado pessoal que
        # não cumpriram a regra (privacidade=dado pessoal + seguranca=confidencial)
        # ficam pendentes aqui em vez de serem aplicadas direto no Unity Catalog.
        f"CREATE TABLE IF NOT EXISTS {_cad('tag_backlog')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, "
        f"catalogo STRING, db_schema STRING, tabela STRING, coluna STRING, "
        f"tag_chave STRING, valor_anterior STRING, valor_novo STRING, acao STRING, "
        f"motivo STRING, solicitante STRING, status STRING, "
        f"aprovador STRING, decidido_em TIMESTAMP, motivo_decisao STRING, "
        f"ambiente STRING, criado_em TIMESTAMP)",
        # Glossário de negócio e indicadores — duas telas de edição, duas
        # tabelas. `glossario_negocio` guarda os termos comuns; `indicadores`
        # acrescenta os campos exclusivos de KPI (nivel_apuracao, unidade,
        # variaveis_utilizadas, memoria_calculo, restricoes) e o par
        # dimensao_tabelas/metrica_tabelas — cada um uma lista JSON de
        # [{"catalogo","schema","tabela","colunas":[...]}] montada no picker de
        # tabelas/colunas. A coluna `tipo` (valor fixo 'Termo'/'Indicador') é
        # redundante mas mantida nas duas pra tela de consulta e card de
        # detalhe (que leem `tipo`) e a união em list_termos_negocio não
        # precisarem ramificar. Migração da antiga `termos_negocio` logo abaixo.
        f"CREATE TABLE IF NOT EXISTS {_ont('glossario_negocio')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, "
        f"tipo STRING, nome STRING, objetivo STRING, observacoes STRING, "
        f"palavras_chave STRING, macroprocesso STRING, "
        f"dominio_id BIGINT, subdominio_id BIGINT, data_owner STRING, data_steward STRING, "
        f"rotulo_seguranca STRING, rotulo_privacidade STRING, {audit})",
        f"CREATE TABLE IF NOT EXISTS {_ont('indicadores')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, "
        f"tipo STRING, nome STRING, objetivo STRING, observacoes STRING, "
        f"palavras_chave STRING, macroprocesso STRING, "
        f"dominio_id BIGINT, subdominio_id BIGINT, "
        f"power_steward STRING, data_owner STRING, data_steward STRING, "
        f"rotulo_seguranca STRING, rotulo_privacidade STRING, "
        f"nivel_apuracao STRING, unidade STRING, variaveis_utilizadas STRING, "
        f"memoria_calculo STRING, restricoes STRING, "
        f"dimensao_tabelas STRING, metrica_tabelas STRING, {audit})",
    ]
    for stmt in ddl:
        run_exec(stmt)  # SP
    # Colunas de permissão granular em `permissoes` (idempotente p/ tabelas já
    # existentes): ver_logs libera as telas de Auditoria; ver_cadastros libera
    # o grupo Cadastros. Admin ignora as flags (enxerga tudo). Como o runtime não
    # aceita `ADD COLUMN IF NOT EXISTS`, checamos o information_schema antes.
    try:
        cols_df = run_query(
            f"SELECT lower(column_name) AS c FROM {q_ident(CAD_CATALOG)}.information_schema.columns "
            f"WHERE lower(table_schema) = {q_str(CAD_SCHEMA.lower())} "
            f"AND lower(table_name) = 'permissoes'"
        )
        existing_cols = set(cols_df["c"].tolist()) if not cols_df.empty else set()
        for col in ("ver_logs", "ver_cadastros", "aprovador_tags", "power_steward"):
            if col not in existing_cols:
                run_exec(f"ALTER TABLE {_cad('permissoes')} ADD COLUMNS ({col} BOOLEAN)")
        if "nome" not in existing_cols:  # nome de exibição do usuário
            run_exec(f"ALTER TABLE {_cad('permissoes')} ADD COLUMNS (nome STRING)")
    except Exception:
        pass
    # Coluna `tipo` em `data_stewards` (idempotente p/ tabelas já existentes,
    # criadas antes de unificar Owner e Steward no mesmo cadastro). Registros
    # antigos (sem tipo) eram todos stewards.
    try:
        cols_df = run_query(
            f"SELECT lower(column_name) AS c FROM {q_ident(CAD_CATALOG)}.information_schema.columns "
            f"WHERE lower(table_schema) = {q_str(CAD_SCHEMA.lower())} "
            f"AND lower(table_name) = 'data_stewards'"
        )
        existing_cols = set(cols_df["c"].tolist()) if not cols_df.empty else set()
        if "tipo" not in existing_cols:
            run_exec(f"ALTER TABLE {_cad('data_stewards')} ADD COLUMNS (tipo STRING)")
            run_exec(f"UPDATE {_cad('data_stewards')} SET tipo = 'Steward' WHERE tipo IS NULL")
    except Exception:
        pass
    # Coluna `power_steward` em `indicadores` (idempotente p/ a tabela já
    # existente). Guarda o e-mail do Power Steward escolhido — a lista vem de
    # `permissoes` (flag `power_steward`).
    try:
        cols_df = run_query(
            f"SELECT lower(column_name) AS c FROM {q_ident(CAD_CATALOG)}.information_schema.columns "
            f"WHERE lower(table_schema) = {q_str(ONTOLOGIA_SCHEMA.lower())} "
            f"AND lower(table_name) = 'indicadores'"
        )
        existing_cols = set(cols_df["c"].tolist()) if not cols_df.empty else set()
        if "power_steward" not in existing_cols:
            run_exec(f"ALTER TABLE {_ont('indicadores')} ADD COLUMNS (power_steward STRING)")
    except Exception:
        pass
    # Migração da antiga `termos_negocio` (registro único com seletor de tipo)
    # para as duas tabelas novas. Roda uma vez: copia por tipo e dropa a origem.
    # Idempotente — se `termos_negocio` não existe mais, não faz nada. Se algum
    # INSERT falhar, o DROP não roda e o próximo boot tenta de novo.
    try:
        tbl_df = run_query(
            f"SELECT lower(table_name) AS t FROM {q_ident(CAD_CATALOG)}.information_schema.tables "
            f"WHERE lower(table_schema) = {q_str(ONTOLOGIA_SCHEMA.lower())} "
            f"AND lower(table_name) = 'termos_negocio'"
        )
        if not tbl_df.empty:
            _comuns = (
                "nome, objetivo, observacoes, palavras_chave, macroprocesso, "
                "dominio_id, subdominio_id, data_owner, data_steward, "
                "rotulo_seguranca, rotulo_privacidade, "
                "criado_em, criado_por, atualizado_em, atualizado_por"
            )
            _ind = (
                "nivel_apuracao, unidade, variaveis_utilizadas, memoria_calculo, restricoes"
            )
            # Cada destino é preenchido só se ainda estiver vazio — assim, se um
            # INSERT falhar, o próximo boot retoma esse sem duplicar o que já foi.
            if _count(f"SELECT count(*) FROM {_ont('glossario_negocio')}") == 0:
                run_exec(
                    f"INSERT INTO {_ont('glossario_negocio')} (tipo, {_comuns}) "
                    f"SELECT 'Termo', {_comuns} FROM {_ont('termos_negocio')} "
                    f"WHERE lower(coalesce(tipo, 'termo')) <> 'indicador'"
                )
            if _count(f"SELECT count(*) FROM {_ont('indicadores')}") == 0:
                run_exec(
                    f"INSERT INTO {_ont('indicadores')} "
                    f"(tipo, {_comuns}, {_ind}, dimensao_tabelas, metrica_tabelas) "
                    f"SELECT 'Indicador', {_comuns}, {_ind}, "
                    f"coalesce(dimensao_tabelas, '[]'), coalesce(metrica_tabelas, '[]') "
                    f"FROM {_ont('termos_negocio')} WHERE lower(tipo) = 'indicador'"
                )
            # Só dropa a origem depois que os dois INSERTs acima passaram sem
            # exceção (o try/except garante isso).
            run_exec(f"DROP TABLE IF EXISTS {_ont('termos_negocio')}")
    except Exception:
        pass
    # Semeia o admin inicial se a tabela de permissões estiver vazia.
    df = run_query(f"SELECT count(*) AS n FROM {_cad('permissoes')}")
    if not df.empty and int(df.iloc[0, 0]) == 0 and SEED_ADMIN_EMAIL:
        run_exec(
            f"INSERT INTO {_cad('permissoes')} (email, papel, criado_em, criado_por) "
            f"VALUES ({q_str(SEED_ADMIN_EMAIL)}, 'admin', current_timestamp(), 'system')"
        )
    return True


def _as_bool(v) -> bool:
    """Interpreta valores vindos do SQL (str 'true'/'false', bool) como bool."""
    return str(v).strip().lower() in ("true", "1", "t", "yes")


@st.cache_data(ttl=60, show_spinner=False)
def get_user_perms(email: str) -> dict:
    """Papel + flags de acesso do usuário. Admin implica ver tudo (menos
    `power_steward`, que é sempre lido do banco — admin não vira Power Steward
    automaticamente).

    Retorna ``{"papel", "ver_logs", "ver_cadastros", "aprovador_tags",
    "power_steward"}``. Para não-admin, as flags vêm das colunas homônimas de
    ``permissoes`` (default False).
    """
    base = {
        "papel": "leitor", "ver_logs": False, "ver_cadastros": False,
        "aprovador_tags": False, "power_steward": False, "registrado": False, "nome": "",
    }
    if not email:
        return base
    df = run_query(
        f"SELECT coalesce(nome, '') AS nome, papel, ver_logs, ver_cadastros, "
        f"aprovador_tags, power_steward "
        f"FROM {_cad('permissoes')} WHERE lower(email) = {q_str(email.lower())} LIMIT 1"
    )
    if df.empty:
        return base
    row = df.iloc[0]
    papel = (row["papel"] or "leitor").strip().lower()
    power_steward = _as_bool(row["power_steward"])
    nome = str(row["nome"] or "").strip()
    if papel == "admin":
        return {
            "papel": "admin", "ver_logs": True, "ver_cadastros": True,
            "aprovador_tags": True, "power_steward": power_steward,
            "registrado": True, "nome": nome,
        }
    return {
        "papel": papel,
        "ver_logs": _as_bool(row["ver_logs"]),
        "ver_cadastros": _as_bool(row["ver_cadastros"]),
        "aprovador_tags": _as_bool(row["aprovador_tags"]),
        "power_steward": power_steward,
        "registrado": True,
        "nome": nome,
    }


def can_edit(role: str) -> bool:
    return role in ("admin", "editor")


# ---- Leituras (SP; cache curto + refresh manual na sidebar) ----
@st.cache_data(ttl=30, show_spinner=False)
def list_dominios() -> pd.DataFrame:
    return run_query(f"SELECT id, nome, descricao FROM {_cad('dominios')} ORDER BY nome")


@st.cache_data(ttl=30, show_spinner=False)
def list_subdominios() -> pd.DataFrame:
    return run_query(
        f"SELECT id, dominio_id, nome, descricao FROM {_cad('subdominios')} ORDER BY nome"
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_stewards() -> pd.DataFrame:
    return run_query(
        f"SELECT id, coalesce(tipo, 'Steward') AS tipo, dominio_id, subdominio_id, nome, email "
        f"FROM {_cad('data_stewards')} ORDER BY nome"
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_dashboards() -> pd.DataFrame:
    return run_query(
        f"SELECT id, dominio_id, subdominio_id, nome, descricao, url, icone, "
        f"coalesce(ativo,true) AS ativo FROM {_cad('dashboards')} ORDER BY nome"
    )


# Colunas comuns às duas telas do glossário (ordem estável — usada nos SELECTs
# e para casar com o INSERT/UPDATE das telas de edição).
_GLOSSARIO_COLS_COMUNS = (
    "id, tipo, nome, objetivo, observacoes, palavras_chave, macroprocesso, "
    "dominio_id, subdominio_id, data_owner, data_steward, "
    "rotulo_seguranca, rotulo_privacidade"
)


@st.cache_data(ttl=30, show_spinner=False)
def list_glossario_negocio() -> pd.DataFrame:
    return run_query(
        f"SELECT {_GLOSSARIO_COLS_COMUNS} FROM {_ont('glossario_negocio')} ORDER BY nome"
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_indicadores() -> pd.DataFrame:
    return run_query(
        f"SELECT {_GLOSSARIO_COLS_COMUNS}, power_steward, nivel_apuracao, unidade, "
        f"variaveis_utilizadas, memoria_calculo, restricoes, "
        f"dimensao_tabelas, metrica_tabelas FROM {_ont('indicadores')} ORDER BY nome"
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_termos_negocio() -> pd.DataFrame:
    """União das duas tabelas do glossário — para a tela de consulta (só
    leitura) e para o Assistente de IA. O lado do glossário projeta os campos
    exclusivos de indicador como vazios."""
    return run_query(
        f"SELECT {_GLOSSARIO_COLS_COMUNS}, CAST(NULL AS STRING) AS power_steward, "
        f"CAST(NULL AS STRING) AS nivel_apuracao, CAST(NULL AS STRING) AS unidade, "
        f"CAST(NULL AS STRING) AS variaveis_utilizadas, "
        f"CAST(NULL AS STRING) AS memoria_calculo, CAST(NULL AS STRING) AS restricoes, "
        f"'[]' AS dimensao_tabelas, '[]' AS metrica_tabelas "
        f"FROM {_ont('glossario_negocio')} "
        f"UNION ALL "
        f"SELECT {_GLOSSARIO_COLS_COMUNS}, power_steward, nivel_apuracao, unidade, "
        f"variaveis_utilizadas, memoria_calculo, restricoes, "
        f"dimensao_tabelas, metrica_tabelas FROM {_ont('indicadores')} "
        f"ORDER BY nome"
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_permissoes() -> pd.DataFrame:
    return run_query(
        f"SELECT id, coalesce(nome, '') AS nome, email, papel, "
        f"coalesce(ver_cadastros,false) AS ver_cadastros, "
        f"coalesce(ver_logs,false) AS ver_logs, coalesce(aprovador_tags,false) AS aprovador_tags, "
        f"coalesce(power_steward,false) AS power_steward "
        f"FROM {_cad('permissoes')} ORDER BY email"
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_padroes_dado_pessoal() -> pd.DataFrame:
    return run_query(
        f"SELECT id, padrao, descricao FROM {_cad('padroes_dado_pessoal')} ORDER BY padrao"
    )


@st.cache_data(ttl=15, show_spinner=False)
def list_tag_backlog(status: str | None = None) -> pd.DataFrame:
    where = f"WHERE status = {q_str(status)}" if status else ""
    return run_query(
        f"SELECT id, catalogo, db_schema, tabela, coluna, tag_chave, valor_anterior, "
        f"valor_novo, acao, motivo, solicitante, status, aprovador, decidido_em, "
        f"motivo_decisao, criado_em FROM {_cad('tag_backlog')} {where} ORDER BY criado_em DESC"
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_log_comentarios(limit: int = 1000) -> pd.DataFrame:
    """Log de auditoria das alterações de comentário (mais recentes primeiro)."""
    return run_query(
        f"SELECT criado_em, usuario, acao, objeto, catalogo, db_schema, tabela, coluna, "
        f"comentario_anterior, comentario_novo, ambiente "
        f"FROM {_cad('log_comentarios')} ORDER BY criado_em DESC LIMIT {int(limit)}"
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_log_tags(limit: int = 1000) -> pd.DataFrame:
    """Log de auditoria das alterações de tag (mais recentes primeiro)."""
    return run_query(
        f"SELECT criado_em, usuario, acao, catalogo, db_schema, tabela, coluna, "
        f"tag_chave, valor_anterior, valor_novo, ambiente "
        f"FROM {_cad('log_tags')} ORDER BY criado_em DESC LIMIT {int(limit)}"
    )


@st.cache_data(ttl=600, show_spinner=False)
def list_workspace_users() -> list[dict]:
    """Usuários do workspace (nome + email) para a busca do steward. Via SP.

    Retorna [] se o SP não puder listar usuários — o cadastro cai para entrada
    manual de nome/e-mail.
    """
    w = get_client()
    users: list[dict] = []
    try:
        for u in w.users.list(attributes="userName,displayName,active"):
            if u.active is False:
                continue
            email = (u.user_name or "").strip()
            if not email:
                continue
            users.append({"nome": (u.display_name or email).strip(), "email": email})
    except Exception:
        return []
    seen, res = set(), []
    for x in sorted(users, key=lambda d: d["nome"].lower()):
        if x["email"].lower() in seen:
            continue
        seen.add(x["email"].lower())
        res.append(x)
    return res


@st.cache_data(ttl=600, show_spinner=False)
def list_account_users() -> list[dict]:
    """Usuários no nível de CONTA via Account SCIM API. Via SP.

    Cobre usuários que ainda não foram provisionados neste workspace (ex.:
    existem só em DEV/na conta ao cadastrar em PRD). Retorna [] se a Account
    API não estiver configurada (sem DATABRICKS_ACCOUNT_ID) ou se o SP não
    tiver permissão de leitura de usuários na conta.
    """
    a = get_account_client()
    if a is None:
        return []
    users: list[dict] = []
    try:
        for u in a.users.list(attributes="userName,displayName,active"):
            if u.active is False:
                continue
            email = (u.user_name or "").strip()
            if not email:
                continue
            users.append({"nome": (u.display_name or email).strip(), "email": email})
    except Exception:
        return []
    return users


@st.cache_data(ttl=600, show_spinner=False)
def list_users_for_search() -> list[dict]:
    """União workspace + conta (dedup por e-mail) para a busca de usuário.

    O workspace vem primeiro (nomes tendem a estar mais completos); a conta
    complementa com quem ainda não foi provisionado no workspace local.
    """
    seen, res = set(), []
    for x in list_workspace_users() + list_account_users():
        k = x["email"].lower()
        if k in seen:
            continue
        seen.add(k)
        res.append(x)
    return sorted(res, key=lambda d: d["nome"].lower())


def _clear_cad_caches() -> None:
    for f in (
        list_dominios, list_subdominios, list_stewards, list_permissoes,
        list_dashboards, list_padroes_dado_pessoal, list_tag_backlog, get_user_perms,
        list_glossario_negocio, list_indicadores, list_termos_negocio, _novos_na_semana,
    ):
        try:
            f.clear()
        except Exception:
            pass


def _finish_write(msg: str) -> None:
    st.session_state["cad_feedback"] = ("success", f"✅ {msg}")
    _clear_cad_caches()
    st.rerun()


def _show_cad_feedback() -> None:
    fb = st.session_state.pop("cad_feedback", None)
    if fb:
        (st.success if fb[0] == "success" else st.error)(fb[1])


def _count(sql: str) -> int:
    df = run_query(sql)
    return int(df.iloc[0, 0]) if not df.empty else 0


# ---------------------------------------------------------------------------
# Assistente de Governança (chat com IA)
# ---------------------------------------------------------------------------
# Painel de chat com function-calling sobre os dados que o próprio app já
# expõe (tags, comentários, cadastros, backlog, auditoria). v1 é SOMENTE
# LEITURA: o assistente explica/consulta, mas quem aplica tag ou grava
# comentário continua sendo o usuário pela tela normal — evita dar poder de
# escrita ao LLM antes de validar o comportamento dele em produção.

_DF_ROW_CAP = 200  # teto de linhas por resultado de tool, pra não estourar o contexto do modelo


def _df_records(df: pd.DataFrame, cap: int = _DF_ROW_CAP) -> list[dict]:
    return df.head(cap).to_dict("records")


ASSISTANT_SYSTEM_PROMPT = """Você é o assistente do app de Governança de Dados — Unity Catalog.

Você ajuda usuários de negócio e da governança a entender e consultar o que já
está registrado no app: tags governadas e comentários aplicados em tabelas e
colunas do Unity Catalog, os cadastros de domínios/sub-domínios/data stewards/
dashboards, o glossário de termos de negócio e indicadores, os padrões que
classificam uma coluna como dado pessoal, o backlog de aprovação de tags e os
logs de auditoria.

Regras importantes:
- Você é SOMENTE CONSULTA. Você não aplica tag, não grava comentário, não
  cadastra/edita termo de negócio e não aprova/rejeita item de backlog — se o
  usuário pedir uma dessas ações, explique que ele mesmo faz isso pela tela do
  app (Governança, Glossário → Termos de Negócio para consultar, Cadastros →
  Glossário de Negócio / Indicador para editar, ou Aprovações → Backlog) e,
  se ajudar, oriente o caminho.
- Use as ferramentas disponíveis para responder com dados reais em vez de
  chutar. Se uma pergunta pedir dados de uma tabela específica, peça
  catalog/schema/table se o usuário não tiver informado.
- Se não tiver uma ferramenta ou dado que cubra a pergunta, diga claramente
  que não tem essa informação em vez de inventar.
- Responda em português, de forma direta e objetiva.
"""

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "tags_e_comentarios_da_tabela",
            "description": (
                "Comentário da tabela e, para cada coluna, seu comentário e as tags "
                "governadas aplicadas. Requer catalog/schema/table exatos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "catalog": {"type": "string"},
                    "schema": {"type": "string"},
                    "table": {"type": "string"},
                },
                "required": ["catalog", "schema", "table"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tags_governadas_disponiveis",
            "description": "Catálogo de tags governadas (Governed Tags) e seus valores permitidos.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dominios_e_subdominios",
            "description": "Domínios e sub-domínios cadastrados no app.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "data_stewards",
            "description": (
                "Data owners e data stewards cadastrados (campo 'tipo' distingue os "
                "dois), com o domínio/sub-domínio de cada um."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dashboards_cadastrados",
            "description": "Dashboards (AI/BI) cadastrados no app, com domínio/sub-domínio.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "padroes_de_dado_pessoal",
            "description": "Padrões (palavras-chave) que classificam uma coluna como dado pessoal.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "termos_de_negocio",
            "description": (
                "Glossário de negócio e indicadores cadastrados (as duas telas "
                "de edição juntas): tipo (Termo/Indicador), nome, definição/"
                "objetivo, domínio/sub-domínio, data owner/steward e, para "
                "indicadores, o power steward, rótulos de segurança/privacidade, "
                "variáveis, fórmula (memória de cálculo), restrições e as "
                "tabelas/colunas que compõem a dimensão e a métrica."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backlog_de_aprovacao_de_tags",
            "description": "Itens do backlog de aprovação de tags de dado pessoal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pendente", "aprovado", "rejeitado"],
                        "description": "Filtra por status. Omitido = todos.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_auditoria",
            "description": "Log de auditoria (mais recentes primeiro) de comentários ou de tags alterados.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": ["comentarios", "tags"]},
                    "limit": {"type": "integer", "description": "Máximo de registros (padrão 20, teto 100)."},
                },
                "required": ["tipo"],
            },
        },
    },
]


def _execute_tool(name: str, args: dict, user: str) -> dict:
    """Executa uma tool e devolve um dict serializável (lista de records ou erro)."""
    try:
        if name == "tags_e_comentarios_da_tabela":
            catalog, schema, table = args["catalog"], args["schema"], args["table"]
            columns = get_columns(user, catalog, schema, table)
            applied_tags = get_applied_column_tags(user, catalog, schema, table)
            comment = get_table_comment(user, catalog, schema, table)
            return {
                "comentario_da_tabela": comment,
                "colunas": [
                    {
                        "coluna": c.name,
                        "tipo": c.data_type,
                        "comentario": c.comment,
                        "tags": applied_tags.get(c.name, {}),
                    }
                    for c in columns
                ],
            }
        if name == "tags_governadas_disponiveis":
            return {"tags_governadas": get_governed_tags()}
        if name == "dominios_e_subdominios":
            return {
                "dominios": _df_records(list_dominios()),
                "subdominios": _df_records(list_subdominios()),
            }
        if name == "data_stewards":
            return {"data_stewards": _df_records(list_stewards())}
        if name == "dashboards_cadastrados":
            return {"dashboards": _df_records(list_dashboards())}
        if name == "padroes_de_dado_pessoal":
            return {"padroes_de_dado_pessoal": _df_records(list_padroes_dado_pessoal())}
        if name == "termos_de_negocio":
            return {"termos_de_negocio": _df_records(list_termos_negocio())}
        if name == "backlog_de_aprovacao_de_tags":
            return {"backlog": _df_records(list_tag_backlog(args.get("status")))}
        if name == "log_auditoria":
            limit = min(int(args.get("limit") or 20), 100)
            if args.get("tipo") == "tags":
                return {"log_tags": _df_records(list_log_tags(limit), cap=limit)}
            return {"log_comentarios": _df_records(list_log_comentarios(limit), cap=limit)}
        return {"erro": f"Ferramenta desconhecida: {name}"}
    except Exception as exc:
        return {"erro": str(exc)}


def _extract_text(content) -> str:
    """Normaliza ``message.content`` para texto puro.

    A maioria dos modelos devolve uma string simples, mas alguns (ex.: GPT
    OSS via AI Gateway) devolvem uma lista de blocos — inclusive um bloco
    ``reasoning`` com o raciocínio interno, que NÃO deve aparecer pro
    usuário. Aqui pegamos só os blocos ``text``.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if block_type != "text":
                continue
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts)
    return str(content) if content else ""


def run_assistant_turn(user_text: str, user: str) -> str:
    """Processa uma pergunta do usuário no painel do assistente e devolve a resposta final.

    Faz o loop de tool-calling localmente (até MAX_ITERATIONS idas e vindas);
    só a mensagem final do assistente é persistida no histórico da sessão —
    as mensagens de tool call ficam só dentro deste loop.
    """
    MAX_ITERATIONS = 6
    try:
        client = get_llm_client()
    except Exception as exc:
        return f"Não consegui me conectar ao assistente de IA: {exc}"

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.get("chat_messages", [])
    ]
    messages = [{"role": "system", "content": ASSISTANT_SYSTEM_PROMPT}, *history, {"role": "user", "content": user_text}]

    for _ in range(MAX_ITERATIONS):
        try:
            response = client.chat.completions.create(
                model=LLM_ENDPOINT,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                max_tokens=1500,
            )
        except Exception as exc:
            return f"O assistente de IA falhou ao responder: {exc}"

        msg = response.choices[0].message
        clean_content = _extract_text(msg.content)
        if not msg.tool_calls:
            return clean_content or "(sem resposta)"

        import json as _json

        messages.append({
            "role": "assistant",
            "content": clean_content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            args = _json.loads(tc.function.arguments) if tc.function.arguments else {}
            result = _execute_tool(tc.function.name, args, user)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _json.dumps(result, default=str, ensure_ascii=False),
            })

    return (
        f"Não consegui concluir em {MAX_ITERATIONS} passos — tente uma pergunta mais "
        "simples ou dividida em partes menores."
    )


# Largura do painel do assistente ancorado à direita (px).
_ASSISTANT_DOCK_W = 380

# O painel é um st.container(key="assistant_dock") reposicionado por CSS para
# ficar fixo na borda direita, funcionando como uma segunda sidebar. Quando
# recolhido, some e no lugar aparece uma aba fina (st.container(key=
# "assistant_tab")) no canto superior direito.
# O app roda sempre em tema claro (Databricks Free Edition). Não usamos
# @media (prefers-color-scheme: dark) aqui: se o SO do usuário está em dark
# mode mas o Streamlit renderiza claro, o painel ficaria com fundo escuro e
# texto escuro (ilegível). Fundo claro fixo, igual ao da sidebar esquerda.
_ASSISTANT_DOCK_CSS = f"""
<style>
.st-key-assistant_dock {{
    position: fixed;
    top: 0;
    right: 0;
    width: {_ASSISTANT_DOCK_W}px;
    height: 100vh;
    overflow-y: auto;
    padding: 4.25rem 1rem 1rem 1rem;
    background-color: #f0f2f6;
    border-left: 1px solid rgba(49, 51, 63, 0.2);
    z-index: 90;
}}
[data-testid="stMainBlockContainer"] {{
    padding-right: {_ASSISTANT_DOCK_W + 48}px !important;
}}
@media (max-width: 1100px) {{
    .st-key-assistant_dock {{ width: 320px; }}
    [data-testid="stMainBlockContainer"] {{ padding-right: 360px !important; }}
}}
</style>
"""

_ASSISTANT_TAB_CSS = """
<style>
.st-key-assistant_tab {
    position: fixed;
    top: 4.25rem;
    right: 0;
    left: auto !important;
    /* O container vertical do Streamlit vem com width:100%; sem isto a "aba"
       ocupa a largura toda e o botão cai no canto esquerdo, atrás da sidebar. */
    width: fit-content !important;
    min-width: 0 !important;
    z-index: 1000000;
}
.st-key-assistant_tab button {
    border-top-right-radius: 0;
    border-bottom-right-radius: 0;
    box-shadow: -1px 2px 8px rgba(0, 0, 0, 0.15);
}
</style>
"""


def render_assistant_dock(user: str) -> None:
    """Painel do assistente ancorado à direita, recolhível para uma aba fina."""
    if not st.session_state.get("show_assistant", True):
        with st.container(key="assistant_tab"):
            if st.button("🤖  Assistente", key="assistant_open_btn"):
                st.session_state["show_assistant"] = True
                st.rerun()
        st.markdown(_ASSISTANT_TAB_CSS, unsafe_allow_html=True)
        return

    with st.container(key="assistant_dock"):
        if st.button("→  Recolher", key="assistant_close_btn", use_container_width=True):
            st.session_state["show_assistant"] = False
            st.rerun()
        render_assistant_panel(user)
    st.markdown(_ASSISTANT_DOCK_CSS, unsafe_allow_html=True)


def render_assistant_panel(user: str) -> None:
    st.markdown("### 🤖 Assistente de Governança")
    if not LLM_ENABLED or not LLM_ENDPOINT:
        st.info("Assistente de IA não configurado (`LLM_ENABLED`/`LLM_ENDPOINT`).")
        return
    st.caption("Respostas geradas por IA — confira antes de agir. Só consulta; não aplica tag/comentário.")

    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = []

    if st.button("🧹 Nova conversa", use_container_width=True):
        st.session_state["chat_messages"] = []
        st.rerun()

    def _ask(question: str) -> None:
        # Chama o assistente ANTES de gravar a pergunta em chat_messages: o
        # histórico usado como contexto (run_assistant_turn) é o que já está
        # em chat_messages, e a pergunta atual é adicionada às mensagens só
        # dentro da função — gravar aqui antes duplicaria a última pergunta.
        with st.spinner("Consultando…"):
            answer = run_assistant_turn(question, user)
        st.session_state["chat_messages"].append({"role": "user", "content": question})
        st.session_state["chat_messages"].append({"role": "assistant", "content": answer})
        st.rerun()

    history_box = st.container(height=420)
    with history_box:
        for m in st.session_state["chat_messages"]:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

    if not st.session_state["chat_messages"]:
        with st.expander("💡 Sugestões", expanded=True):
            for label in (
                "Quais domínios existem?",
                "Quais termos de negócio e indicadores estão cadastrados?",
                "O que está pendente no backlog de aprovação de tags?",
                "Quem são os data stewards cadastrados?",
            ):
                if st.button(label, use_container_width=True, key=f"assist_qp_{label}"):
                    _ask(label)

    prompt = st.chat_input("Pergunte ao assistente…")
    if prompt:
        _ask(prompt)


# ---------------------------------------------------------------------------
# Páginas de cadastro
# ---------------------------------------------------------------------------


def page_dominios() -> None:
    st.title("🗂️ Domínios")
    st.caption("Cadastro principal. Sub-domínios, Data Owners e Data Stewards se vinculam a um domínio.")
    _show_cad_feedback()
    role = st.session_state.get("role", "leitor")
    user = st.session_state.get("user", "")

    df = list_dominios()
    st.dataframe(
        df.rename(columns={"id": "ID", "nome": "Nome", "descricao": "Descrição"}),
        use_container_width=True, hide_index=True,
    )

    if not can_edit(role):
        st.info("Seu perfil é **leitor** — visualização apenas.")
        return

    recs = df.to_dict("records")
    opts = ["(novo)"] + [f'{r["nome"]} (id {r["id"]})' for r in recs]
    st.divider()
    st.markdown("#### Adicionar / editar")
    sel = st.selectbox("Registro", options=opts, key="dom_sel")
    editing = sel != "(novo)"
    cur = recs[opts.index(sel) - 1] if editing else {"id": None, "nome": "", "descricao": ""}

    with st.form("form_dom"):
        nome = st.text_input("Nome *", value=cur["nome"] or "")
        desc = st.text_area("Descrição", value=cur.get("descricao") or "")
        saved = st.form_submit_button("💾 Salvar", type="primary")

    if saved:
        nome = (nome or "").strip()
        if not nome:
            st.warning("Informe o nome do domínio.")
            return
        extra = f" AND id <> {int(cur['id'])}" if editing else ""
        if _count(f"SELECT count(*) FROM {_cad('dominios')} WHERE lower(nome) = {q_str(nome.lower())}{extra}"):
            st.error("Já existe um domínio com esse nome.")
            return
        if editing:
            run_exec(
                f"UPDATE {_cad('dominios')} SET nome = {q_str(nome)}, descricao = {q_str(desc)}, "
                f"atualizado_em = current_timestamp(), atualizado_por = {q_str(user)} "
                f"WHERE id = {int(cur['id'])}"
            )
        else:
            # INSERT atômico: o WHERE NOT EXISTS impede duplicata mesmo sob
            # concorrência/cache defasado (a checagem acima é só p/ a mensagem).
            run_exec(
                f"INSERT INTO {_cad('dominios')} (nome, descricao, criado_em, criado_por) "
                f"SELECT {q_str(nome)}, {q_str(desc)}, current_timestamp(), {q_str(user)} "
                f"FROM (SELECT 1) WHERE NOT EXISTS "
                f"(SELECT 1 FROM {_cad('dominios')} WHERE lower(nome) = {q_str(nome.lower())})"
            )
        _finish_write("Domínio salvo.")

    if editing:
        st.divider()
        st.markdown("#### Excluir")
        st.caption("A exclusão é bloqueada se houver sub-domínios ou data stewards vinculados.")
        if st.button(f"🗑️ Excluir domínio '{cur['nome']}'"):
            dep = (
                _count(f"SELECT count(*) FROM {_cad('subdominios')} WHERE dominio_id = {int(cur['id'])}")
                + _count(f"SELECT count(*) FROM {_cad('data_stewards')} WHERE dominio_id = {int(cur['id'])}")
            )
            if dep:
                st.error("Não é possível excluir: há registros vinculados. Remova-os primeiro.")
            else:
                run_exec(f"DELETE FROM {_cad('dominios')} WHERE id = {int(cur['id'])}")
                _finish_write("Domínio excluído.")


def page_subdominios() -> None:
    st.title("🗃️ Sub-domínios")
    st.caption("Cada sub-domínio pertence a um domínio.")
    _show_cad_feedback()
    role = st.session_state.get("role", "leitor")
    user = st.session_state.get("user", "")

    doms = list_dominios().to_dict("records")
    dom_nome = {d["id"]: d["nome"] for d in doms}
    subs = list_subdominios()
    show = subs.copy()
    if not show.empty:
        show["Domínio"] = show["dominio_id"].map(lambda i: dom_nome.get(i, i))
    st.dataframe(
        (show.rename(columns={"id": "ID", "nome": "Nome", "descricao": "Descrição"})
             [["ID", "Domínio", "Nome", "Descrição"]] if not show.empty else show),
        use_container_width=True, hide_index=True,
    )

    if not can_edit(role):
        st.info("Seu perfil é **leitor** — visualização apenas.")
        return
    if not doms:
        st.warning("Cadastre um **Domínio** primeiro.")
        return

    recs = subs.to_dict("records")
    opts = ["(novo)"] + [f'{dom_nome.get(r["dominio_id"], r["dominio_id"])} › {r["nome"]} (id {r["id"]})' for r in recs]
    st.divider()
    st.markdown("#### Adicionar / editar")
    sel = st.selectbox("Registro", options=opts, key="sub_sel")
    editing = sel != "(novo)"
    cur = recs[opts.index(sel) - 1] if editing else {"id": None, "dominio_id": None, "nome": "", "descricao": ""}

    dom_ids = [d["id"] for d in doms]
    idx = dom_ids.index(cur["dominio_id"]) if editing and cur["dominio_id"] in dom_ids else 0
    with st.form("form_sub"):
        dom_id = st.selectbox(
            "Domínio *", options=dom_ids, index=idx,
            format_func=lambda i: dom_nome.get(i, i),
        )
        nome = st.text_input("Nome *", value=cur["nome"] or "")
        desc = st.text_area("Descrição", value=cur.get("descricao") or "")
        saved = st.form_submit_button("💾 Salvar", type="primary")

    if saved:
        nome = (nome or "").strip()
        if not nome:
            st.warning("Informe o nome do sub-domínio.")
            return
        extra = f" AND id <> {int(cur['id'])}" if editing else ""
        if _count(
            f"SELECT count(*) FROM {_cad('subdominios')} WHERE dominio_id = {int(dom_id)} "
            f"AND lower(nome) = {q_str(nome.lower())}{extra}"
        ):
            st.error("Já existe um sub-domínio com esse nome neste domínio.")
            return
        if editing:
            run_exec(
                f"UPDATE {_cad('subdominios')} SET dominio_id = {int(dom_id)}, nome = {q_str(nome)}, "
                f"descricao = {q_str(desc)}, atualizado_em = current_timestamp(), atualizado_por = {q_str(user)} "
                f"WHERE id = {int(cur['id'])}"
            )
        else:
            # INSERT atômico: bloqueia sub-domínio de mesmo nome no mesmo domínio.
            run_exec(
                f"INSERT INTO {_cad('subdominios')} (dominio_id, nome, descricao, criado_em, criado_por) "
                f"SELECT {int(dom_id)}, {q_str(nome)}, {q_str(desc)}, current_timestamp(), {q_str(user)} "
                f"FROM (SELECT 1) WHERE NOT EXISTS "
                f"(SELECT 1 FROM {_cad('subdominios')} WHERE dominio_id = {int(dom_id)} "
                f"AND lower(nome) = {q_str(nome.lower())})"
            )
        _finish_write("Sub-domínio salvo.")

    if editing:
        st.divider()
        st.markdown("#### Excluir")
        if st.button(f"🗑️ Excluir sub-domínio '{cur['nome']}'"):
            if _count(f"SELECT count(*) FROM {_cad('data_stewards')} WHERE subdominio_id = {int(cur['id'])}"):
                st.error("Não é possível excluir: há data stewards vinculados. Remova-os primeiro.")
            else:
                run_exec(f"DELETE FROM {_cad('subdominios')} WHERE id = {int(cur['id'])}")
                _finish_write("Sub-domínio excluído.")


def page_stewards() -> None:
    st.title("🧑‍💼 Data Owners & Stewards")
    st.caption(
        "Cadastro de responsáveis vinculados a um Domínio e Sub-domínio. Escolha "
        "logo abaixo se este registro é um **Data Owner** ou um **Data Steward** — "
        "os dois usam o mesmo cadastro."
    )
    _show_cad_feedback()
    role = st.session_state.get("role", "leitor")
    actor = st.session_state.get("user", "")

    doms = list_dominios().to_dict("records")
    subs = list_subdominios().to_dict("records")
    dom_nome = {d["id"]: d["nome"] for d in doms}
    sub_nome = {s["id"]: s["nome"] for s in subs}
    stw = list_stewards()
    show = stw.copy()
    if not show.empty:
        show["Domínio"] = show["dominio_id"].map(lambda i: dom_nome.get(i, i))
        show["Sub-domínio"] = show["subdominio_id"].map(lambda i: sub_nome.get(i, i))
    st.dataframe(
        (show.rename(columns={"tipo": "Tipo", "nome": "Nome", "email": "E-mail"})
             [["Tipo", "Nome", "E-mail", "Domínio", "Sub-domínio"]] if not show.empty else show),
        use_container_width=True, hide_index=True,
    )

    if not can_edit(role):
        st.info("Seu perfil é **leitor** — visualização apenas.")
        return
    if not doms or not subs:
        st.warning("Cadastre um **Domínio** e um **Sub-domínio** primeiro.")
        return

    st.divider()
    st.markdown("#### Adicionar")

    tipo = st.selectbox("Tipo *", options=_PESSOA_TIPO_OPTIONS, key="stw_tipo")
    tipo_label = tipo.lower()

    # Busca de usuário (reativa — fora de form) → pré-preenche nome + e-mail.
    # A lista une workspace + conta (Account SCIM); se mesmo assim o usuário
    # não aparecer (ou a listagem falhar), o toggle libera a entrada manual.
    users = list_users_for_search()
    nome = email = None
    manual = st.toggle(
        "✍️ Informar manualmente (usuário não encontrado na busca)",
        value=not users, disabled=not users, key="stw_manual",
    )
    if users and not manual:
        term = st.text_input("🔍 Buscar usuário (nome ou e-mail)", key="stw_search")
        if term:
            t = term.lower()
            matches = [u for u in users if t in u["nome"].lower() or t in u["email"].lower()][:50]
            if matches:
                pick = st.selectbox(
                    "Resultado", options=matches,
                    format_func=lambda u: f'{u["nome"]} <{u["email"]}>', key="stw_pick",
                )
                nome, email = pick["nome"], pick["email"]
            else:
                st.caption(
                    "Nenhum usuário encontrado — ative *Informar manualmente* acima "
                    "para digitar nome e e-mail."
                )
    else:
        if not users:
            st.caption("Não foi possível listar usuários (workspace/conta) — informe manualmente.")
        nome = st.text_input("Nome *", key="stw_nome_manual").strip()
        email = st.text_input("E-mail corporativo *", key="stw_email_manual").strip().lower()

    if not manual:
        c1, c2 = st.columns(2)
        with c1:
            st.text_input("Nome", value=nome or "", disabled=True, key="stw_nome_view")
        with c2:
            st.text_input("E-mail", value=email or "", disabled=True, key="stw_email_view")

    dom_ids = [d["id"] for d in doms]
    dom_id = st.selectbox("Domínio *", options=dom_ids, format_func=lambda i: dom_nome.get(i, i), key="stw_dom")
    sub_ids = [s["id"] for s in subs if s["dominio_id"] == dom_id]
    if not sub_ids:
        st.warning("Este domínio não tem sub-domínios. Cadastre um sub-domínio primeiro.")
    sub_id = st.selectbox(
        "Sub-domínio *", options=sub_ids, format_func=lambda i: sub_nome.get(i, i),
        key="stw_sub", disabled=not sub_ids,
    ) if sub_ids else None

    if st.button(f"💾 Adicionar {tipo_label}", type="primary", disabled=not sub_ids):
        if not (nome and email):
            st.warning("Selecione/informe o usuário (nome e e-mail).")
            return
        if "@" not in email or " " in email:
            st.warning("Informe um e-mail corporativo válido.")
            return
        if _count(
            f"SELECT count(*) FROM {_cad('data_stewards')} WHERE tipo = {q_str(tipo)} "
            f"AND dominio_id = {int(dom_id)} "
            f"AND subdominio_id = {int(sub_id)} AND lower(email) = {q_str(email.lower())}"
        ):
            st.error(f"Esse {tipo_label} já está vinculado a este domínio/sub-domínio.")
            return
        # INSERT atômico: bloqueia o mesmo e-mail (+ tipo) no mesmo domínio/sub-domínio.
        run_exec(
            f"INSERT INTO {_cad('data_stewards')} (tipo, dominio_id, subdominio_id, nome, email, criado_em, criado_por) "
            f"SELECT {q_str(tipo)}, {int(dom_id)}, {int(sub_id)}, {q_str(nome)}, {q_str(email)}, current_timestamp(), {q_str(actor)} "
            f"FROM (SELECT 1) WHERE NOT EXISTS "
            f"(SELECT 1 FROM {_cad('data_stewards')} WHERE tipo = {q_str(tipo)} "
            f"AND dominio_id = {int(dom_id)} "
            f"AND subdominio_id = {int(sub_id)} AND lower(email) = {q_str(email.lower())})"
        )
        _finish_write(f"{tipo} adicionado.")

    # Excluir
    recs = stw.to_dict("records")
    if recs:
        st.divider()
        st.markdown("#### Excluir")
        opts = [
            f'[{r["tipo"]}] {r["nome"]} <{r["email"]}> — {dom_nome.get(r["dominio_id"], r["dominio_id"])} › '
            f'{sub_nome.get(r["subdominio_id"], r["subdominio_id"])} (id {r["id"]})'
            for r in recs
        ]
        sel = st.selectbox("Registro", options=opts, key="stw_del_sel")
        if st.button("🗑️ Excluir registro selecionado"):
            rid = recs[opts.index(sel)]["id"]
            run_exec(f"DELETE FROM {_cad('data_stewards')} WHERE id = {int(rid)}")
            _finish_write("Registro excluído.")


def page_dashboards() -> None:
    st.title("📊 Dashboards")
    st.caption(
        "Cadastro de dashboards AI/BI (Lakeview) publicados. Cada um pertence a um "
        "Domínio (e opcionalmente a um Sub-domínio) — quem enxerga o link no menu "
        "Governança é quem for **admin** ou **Data Steward** daquele domínio/sub-domínio."
    )
    _show_cad_feedback()
    role = st.session_state.get("role", "leitor")
    user = st.session_state.get("user", "")

    doms = list_dominios().to_dict("records")
    subs = list_subdominios().to_dict("records")
    dom_nome = {d["id"]: d["nome"] for d in doms}
    sub_nome = {s["id"]: s["nome"] for s in subs}
    dash = list_dashboards()
    show = dash.copy()
    if not show.empty:
        show["Domínio"] = show["dominio_id"].map(lambda i: dom_nome.get(i, i))
        show["Sub-domínio"] = show["subdominio_id"].map(
            lambda i: sub_nome.get(i, "(todos)") if pd.notna(i) else "(todos)"
        )
    st.dataframe(
        (show.rename(columns={
            "nome": "Nome", "descricao": "Descrição", "url": "URL",
            "icone": "Ícone", "ativo": "Ativo",
        })[["Nome", "Domínio", "Sub-domínio", "URL", "Ícone", "Ativo", "Descrição"]]
         if not show.empty else show),
        use_container_width=True, hide_index=True,
    )

    if not can_edit(role):
        st.info("Seu perfil é **leitor** — visualização apenas.")
        return
    if not doms:
        st.warning("Cadastre um **Domínio** primeiro.")
        return

    recs = dash.to_dict("records")
    opts = ["(novo)"] + [f'{r["nome"]} (id {r["id"]})' for r in recs]
    st.divider()
    st.markdown("#### Adicionar / editar")
    sel = st.selectbox("Registro", options=opts, key="dash_sel")
    editing = sel != "(novo)"
    cur = (
        recs[opts.index(sel) - 1] if editing
        else {
            "id": None, "dominio_id": None, "subdominio_id": None, "nome": "",
            "descricao": "", "url": "", "icone": "📊", "ativo": True,
        }
    )

    dom_ids = [d["id"] for d in doms]
    dom_idx = dom_ids.index(cur["dominio_id"]) if editing and cur["dominio_id"] in dom_ids else 0
    with st.form("form_dash"):
        nome = st.text_input("Nome *", value=cur["nome"] or "")
        url = st.text_input("URL do dashboard publicado *", value=cur.get("url") or "")
        dom_id = st.selectbox(
            "Domínio *", options=dom_ids, index=dom_idx, format_func=lambda i: dom_nome.get(i, i),
        )
        sub_ids_all = [s["id"] for s in subs if s["dominio_id"] == dom_id]
        sub_options = [None] + sub_ids_all
        cur_sub = cur.get("subdominio_id")
        sub_idx = sub_options.index(cur_sub) if editing and cur_sub in sub_options else 0
        sub_id = st.selectbox(
            "Sub-domínio (opcional — vazio libera para todo o domínio)",
            options=sub_options, index=sub_idx,
            format_func=lambda i: "(todos os sub-domínios)" if i is None else sub_nome.get(i, i),
        )
        c1, c2 = st.columns(2)
        with c1:
            icone = st.text_input("Ícone (emoji)", value=cur.get("icone") or "📊")
        with c2:
            ativo = st.checkbox("Ativo", value=bool(cur.get("ativo", True)))
        desc = st.text_area("Descrição", value=cur.get("descricao") or "")
        saved = st.form_submit_button("💾 Salvar", type="primary")

    if saved:
        nome = (nome or "").strip()
        url = (url or "").strip()
        if not nome or not url:
            st.warning("Informe nome e URL do dashboard.")
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            st.warning("A URL deve começar com http:// ou https://.")
            return
        sub_sql = "NULL" if sub_id is None else str(int(sub_id))
        if editing:
            run_exec(
                f"UPDATE {_cad('dashboards')} SET dominio_id = {int(dom_id)}, "
                f"subdominio_id = {sub_sql}, nome = {q_str(nome)}, descricao = {q_str(desc)}, "
                f"url = {q_str(url)}, icone = {q_str(icone or '📊')}, ativo = {str(bool(ativo)).lower()}, "
                f"atualizado_em = current_timestamp(), atualizado_por = {q_str(user)} "
                f"WHERE id = {int(cur['id'])}"
            )
        else:
            run_exec(
                f"INSERT INTO {_cad('dashboards')} "
                f"(dominio_id, subdominio_id, nome, descricao, url, icone, ativo, criado_em, criado_por) "
                f"VALUES ({int(dom_id)}, {sub_sql}, {q_str(nome)}, {q_str(desc)}, {q_str(url)}, "
                f"{q_str(icone or '📊')}, {str(bool(ativo)).lower()}, current_timestamp(), {q_str(user)})"
            )
        _finish_write("Dashboard salvo.")

    if editing:
        st.divider()
        st.markdown("#### Excluir")
        if st.button(f"🗑️ Excluir dashboard '{cur['nome']}'"):
            run_exec(f"DELETE FROM {_cad('dashboards')} WHERE id = {int(cur['id'])}")
            _finish_write("Dashboard excluído.")


def page_padroes_dado_pessoal() -> None:
    st.title("🧬 Padrões de Dado Pessoal")
    st.caption(
        "Palavras/trechos (case-insensitive) que, ao aparecerem no nome de uma "
        "coluna, classificam-na como dado pessoal — ex.: o padrão 'cpf' casa com "
        "'numero_cpf', 'cpf_cliente' etc. Colunas classificadas como dado pessoal "
        "exigem as tags governadas **privacidade = dado pessoal** e "
        "**seguranca = confidencial**; tagueamento fora dessa regra vai para o "
        "backlog de aprovação em vez de ser aplicado direto."
    )
    _show_cad_feedback()
    role = st.session_state.get("role", "leitor")
    user = st.session_state.get("user", "")

    df = list_padroes_dado_pessoal()
    st.dataframe(
        df.rename(columns={"id": "ID", "padrao": "Padrão", "descricao": "Descrição"}),
        use_container_width=True, hide_index=True,
    )

    if not can_edit(role):
        st.info("Seu perfil é **leitor** — visualização apenas.")
        return

    recs = df.to_dict("records")
    opts = ["(novo)"] + [f'{r["padrao"]} (id {r["id"]})' for r in recs]
    st.divider()
    st.markdown("#### Adicionar / editar")
    sel = st.selectbox("Registro", options=opts, key="pdp_sel")
    editing = sel != "(novo)"
    cur = recs[opts.index(sel) - 1] if editing else {"id": None, "padrao": "", "descricao": ""}

    with st.form("form_pdp"):
        padrao = st.text_input("Padrão *", value=cur["padrao"] or "", help="Ex.: cpf, rg, nome, email, telefone")
        desc = st.text_area("Descrição", value=cur.get("descricao") or "")
        saved = st.form_submit_button("💾 Salvar", type="primary")

    if saved:
        padrao = (padrao or "").strip().lower()
        if not padrao:
            st.warning("Informe o padrão.")
            return
        extra = f" AND id <> {int(cur['id'])}" if editing else ""
        if _count(f"SELECT count(*) FROM {_cad('padroes_dado_pessoal')} WHERE lower(padrao) = {q_str(padrao)}{extra}"):
            st.error("Esse padrão já está cadastrado.")
            return
        if editing:
            run_exec(
                f"UPDATE {_cad('padroes_dado_pessoal')} SET padrao = {q_str(padrao)}, "
                f"descricao = {q_str(desc)}, atualizado_em = current_timestamp(), "
                f"atualizado_por = {q_str(user)} WHERE id = {int(cur['id'])}"
            )
        else:
            run_exec(
                f"INSERT INTO {_cad('padroes_dado_pessoal')} (padrao, descricao, criado_em, criado_por) "
                f"SELECT {q_str(padrao)}, {q_str(desc)}, current_timestamp(), {q_str(user)} "
                f"FROM (SELECT 1) WHERE NOT EXISTS "
                f"(SELECT 1 FROM {_cad('padroes_dado_pessoal')} WHERE lower(padrao) = {q_str(padrao)})"
            )
        _finish_write("Padrão salvo.")

    if editing:
        st.divider()
        st.markdown("#### Excluir")
        if st.button(f"🗑️ Excluir padrão '{cur['padrao']}'"):
            run_exec(f"DELETE FROM {_cad('padroes_dado_pessoal')} WHERE id = {int(cur['id'])}")
            _finish_write("Padrão excluído.")


_NIVEL_APURACAO_OPTIONS = ["", "Diário", "Semanal", "Mensal", "Trimestral", "Semestral", "Anual", "Sob demanda"]
_TERMO_TIPO_OPTIONS = ["Termo", "Indicador"]
_UNIDADE_OPTIONS = ["", "R$", "%", "un", "dias", "horas", "quantidade", "índice", "score", "Outra…"]
_PESSOA_TIPO_OPTIONS = ["Steward", "Owner"]


def _parse_tabelas_json(raw: str | None) -> list[dict]:
    """Desserializa a lista de tabelas/colunas (dimensão ou métrica de um indicador)."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _dump_tabelas_json(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False)


def _sync_tabela_picker_state(kind: str, record_key: str, initial: list[dict]) -> None:
    """(Re)carrega o estado do picker de tabelas/colunas quando o registro em
    edição muda — pra não misturar seleções de um termo com as de outro."""
    marker_key = f"term_{kind}_items_for"
    items_key = f"term_{kind}_items"
    if st.session_state.get(marker_key) != record_key:
        st.session_state[items_key] = [dict(it) for it in initial]
        st.session_state[marker_key] = record_key


def _render_tabela_picker(user: str, kind: str) -> list[dict]:
    """Picker reativo de tabelas + colunas (multi), no mesmo padrão de
    Catalog → Schema → Table do módulo de Governança/Catalogação. Cada tabela
    adicionada pode trazer uma ou mais colunas (vazio = tabela inteira)."""
    items_key = f"term_{kind}_items"
    items: list[dict] = st.session_state.setdefault(items_key, [])
    # "Geração" do formulário de adicionar tabela: incrementada a cada tabela
    # adicionada, trocando as keys dos widgets abaixo — isso garante que eles
    # voltem a nascer em branco (apagar a key do session_state sozinho não é
    # confiável para esse tipo de selectbox).
    gen = st.session_state.get(f"term_{kind}_gen", 0)

    if items:
        for idx, it in enumerate(items):
            cols_txt = ", ".join(it.get("colunas") or []) or "(tabela inteira)"
            c1, c2 = st.columns([8, 1])
            with c1:
                st.caption(f'`{it["catalogo"]}.{it["schema"]}.{it["tabela"]}` — {cols_txt}')
            with c2:
                if st.button("🗑️", key=f"term_{kind}_rm_{idx}"):
                    items.pop(idx)
                    st.rerun()
    else:
        st.caption("Nenhuma tabela adicionada ainda.")

    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            catalogs = list_catalogs(user)
            catalog = st.selectbox(
                "Catalog", options=catalogs, index=None, placeholder="Selecione…",
                key=f"term_{kind}_new_cat_{gen}",
            )
        schema = None
        with c2:
            if catalog:
                try:
                    schemas = [s for s in list_schemas(user, catalog) if schema_belongs_to_env(s)]
                except Exception as exc:
                    schemas = []
                    st.caption(f"⚠️ Sem acesso a schemas de `{catalog}`: {exc}")
                schema = st.selectbox(
                    "Schema", options=schemas, index=None, placeholder="Selecione…",
                    key=f"term_{kind}_new_schema_{gen}",
                )
        table = None
        with c3:
            if catalog and schema:
                try:
                    tables = list_tables(user, catalog, schema)
                except Exception as exc:
                    tables = []
                    st.caption(f"⚠️ Sem acesso a tabelas de `{catalog}.{schema}`: {exc}")
                table = st.selectbox(
                    "Table", options=tables, index=None, placeholder="Selecione…",
                    key=f"term_{kind}_new_table_{gen}",
                )
        if catalog and schema and table:
            try:
                col_names = [c.name for c in get_columns(user, catalog, schema, table)]
            except Exception as exc:
                col_names = []
                st.caption(f"⚠️ Sem acesso a colunas de `{catalog}.{schema}.{table}`: {exc}")
            novas_colunas = st.multiselect(
                "Colunas (vazio = tabela inteira)", options=col_names,
                key=f"term_{kind}_new_cols_{gen}",
            )
            if st.button("➕ Adicionar tabela", key=f"term_{kind}_add_{gen}"):
                items.append({
                    "catalogo": catalog, "schema": schema, "tabela": table,
                    "colunas": novas_colunas,
                })
                st.session_state[f"term_{kind}_gen"] = gen + 1
                st.rerun()
    return items


def _select_pessoa_cadastrada(
    label: str, tipo: str, pessoas: list[dict], dom_id, sub_id, cur_value: str, key_prefix: str,
) -> str:
    """Dropdown de Data Owner/Data Steward a partir do cadastro (mesma tabela,
    filtrada por `tipo`), com fallback de texto livre quando não há domínio
    escolhido ou ninguém cadastrado pra esse domínio/sub-domínio."""
    candidatos = [
        p for p in pessoas
        if p["tipo"] == tipo and dom_id is not None and p["dominio_id"] == dom_id
        and (sub_id is None or p["subdominio_id"] == sub_id)
    ]
    if candidatos:
        opts = ["(nenhum)"] + [f'{p["nome"]} <{p["email"]}>' for p in candidatos]
        idx = opts.index(cur_value) if cur_value in opts else 0
        picked = st.selectbox(label, options=opts, index=idx, key=f"{key_prefix}_sel")
        return "" if picked == "(nenhum)" else picked
    msg = (
        "Selecione um domínio para escolher" if dom_id is None
        else "Ninguém cadastrado para esse domínio/sub-domínio"
    )
    st.caption(f"{msg} — cadastre em Data Owners & Stewards, se necessário.")
    return st.text_input(f"{label} (texto livre)", value=cur_value or "", key=f"{key_prefix}_txt")


def _kw_list_key(kp: str) -> str:
    return f"{kp}_kw_list"


def _sync_keywords_state(kp: str, record_key: str, initial_csv: str) -> None:
    """(Re)carrega a lista de palavras-chave quando o registro em edição muda —
    pra não misturar as palavras de um termo com as de outro."""
    marker = f"{kp}_kw_for"
    if st.session_state.get(marker) != record_key:
        st.session_state[_kw_list_key(kp)] = [
            w.strip() for w in (initial_csv or "").split(",") if w.strip()
        ]
        st.session_state[marker] = record_key
        st.session_state[f"{kp}_kw_input"] = ""


def _add_keyword(kp: str) -> None:
    """Callback do Enter no campo de palavra-chave: adiciona o texto à lista e
    limpa o campo pra digitar a próxima."""
    val = (st.session_state.get(f"{kp}_kw_input") or "").strip()
    lst = st.session_state.setdefault(_kw_list_key(kp), [])
    if val and val.lower() not in {w.lower() for w in lst}:
        lst.append(val)
    st.session_state[f"{kp}_kw_input"] = ""


def _render_power_steward_select(cur_email: str, wkey: str) -> str:
    """Dropdown de Power Steward (tela Indicador). A lista vem dos usuários com
    a flag `power_steward` em Usuários & Permissões — mostra o nome, grava o
    e-mail. Opcional."""
    try:
        perms = list_permissoes().to_dict("records")
    except Exception:
        perms = []
    ps_emails = [p["email"] for p in perms if _as_bool(p.get("power_steward"))]
    # Mantém o valor já gravado mesmo que a flag do usuário tenha sido retirada.
    if cur_email and cur_email not in ps_emails:
        ps_emails = [cur_email] + ps_emails
    try:
        name_by_email = {u["email"].lower(): u["nome"] for u in list_users_for_search()}
    except Exception:
        name_by_email = {}

    def _label(em: str) -> str:
        if not em:
            return "(nenhum)"
        nm = name_by_email.get(em.lower())
        return f"{nm} <{em}>" if nm and nm.lower() != em.lower() else em

    options = [""] + ps_emails
    idx = options.index(cur_email) if cur_email in options else 0
    picked = st.selectbox(
        "Power Steward", options=options, index=idx,
        format_func=_label, key=wkey,
    )
    if not ps_emails:
        st.caption(
            "Ninguém marcado como Power Steward — marque um usuário em "
            "**Usuários & Permissões**."
        )
    return picked


def _render_keyword_chips(kp: str) -> list[str]:
    """Mostra as palavras-chave já adicionadas como 'chips' com um ✕ pra
    remover. Devolve a lista atual (pra gravar como CSV no save)."""
    lst: list[str] = st.session_state.get(_kw_list_key(kp), [])
    if lst:
        st.caption("Palavras-chave adicionadas (clique para remover):")
        per_row = min(len(lst), 4)
        cols = st.columns(per_row)
        for i, w in enumerate(list(lst)):
            with cols[i % per_row]:
                if st.button(f"✕ {w}", key=f"{kp}_kw_rm_{i}", use_container_width=True):
                    lst.pop(i)
                    st.rerun()
    else:
        st.caption("Nenhuma palavra-chave adicionada ainda.")
    return lst


def _render_glossario_editor(
    *, is_indicador: bool, ont_table: str, list_fn, titulo: str, icone: str,
) -> None:
    """Corpo compartilhado das duas telas de edição do glossário (Glossário de
    Negócio e Indicador). `is_indicador` liga os campos exclusivos de KPI e
    escolhe a tabela de destino (`ont_table`)."""
    kp = "ind" if is_indicador else "glo"  # prefixo de key dos widgets (por tela)
    st.title(f"{icone} {titulo}")
    st.caption(
        "Cadastro de indicadores (KPIs): objetivo, dono, unidade, nível de "
        "apuração, memória de cálculo e as tabelas/colunas que compõem a "
        "dimensão e a métrica."
        if is_indicador else
        "Glossário de termos de negócio: nome, definição, palavras-chave, "
        "domínio e responsáveis (Data Owner / Steward)."
    )
    _show_cad_feedback()
    role = st.session_state.get("role", "leitor")
    user = st.session_state.get("user", "")

    doms = list_dominios().to_dict("records")
    subs = list_subdominios().to_dict("records")
    stewards = list_stewards().to_dict("records")
    dom_nome = {d["id"]: d["nome"] for d in doms}
    sub_nome = {s["id"]: s["nome"] for s in subs}
    try:
        governed_tags = get_governed_tags()
    except Exception:
        governed_tags = {}
    seguranca_opts = [""] + governed_tags.get("seguranca", [])
    privacidade_opts = [""] + governed_tags.get("privacidade", [])

    termos = list_fn()
    if termos.empty:
        st.info(f"Nenhum {'indicador' if is_indicador else 'termo'} cadastrado ainda.")
    else:
        show = termos.copy()
        show["Domínio"] = show["dominio_id"].map(lambda i: dom_nome.get(i, "—") if pd.notna(i) else "—")
        show["Sub-domínio"] = show["subdominio_id"].map(lambda i: sub_nome.get(i, "—") if pd.notna(i) else "—")
        cols_show = ["Tipo", "Nome", "Domínio", "Sub-domínio", "Data Owner"]
        if is_indicador:
            cols_show.append("Nível de Apuração")
        st.dataframe(
            show.rename(columns={
                "tipo": "Tipo", "nome": "Nome", "data_owner": "Data Owner",
                "nivel_apuracao": "Nível de Apuração",
            })[cols_show],
            use_container_width=True, hide_index=True,
        )

    if not can_edit(role):
        st.info("Seu perfil é **leitor** — visualização apenas.")
        return

    tipo = "Indicador" if is_indicador else "Termo"
    recs = termos.to_dict("records")
    opts = ["(novo)"] + [f'{r["nome"]} (id {r["id"]})' for r in recs]
    st.divider()
    st.markdown("#### Adicionar / editar")
    sel = st.selectbox("Registro", options=opts, key=f"{kp}_sel")
    editing = sel != "(novo)"
    cur = recs[opts.index(sel) - 1] if editing else {
        "id": None, "tipo": tipo, "nome": "", "objetivo": "", "observacoes": "",
        "palavras_chave": "", "macroprocesso": "", "dominio_id": None, "subdominio_id": None,
        "power_steward": "", "data_owner": "", "data_steward": "",
        "rotulo_seguranca": "", "rotulo_privacidade": "",
        "nivel_apuracao": "", "unidade": "", "variaveis_utilizadas": "",
        "memoria_calculo": "", "restricoes": "", "dimensao_tabelas": "[]", "metrica_tabelas": "[]",
    }

    # As keys dos widgets abaixo levam o id do registro (`_{rk}`): quando o
    # usuário troca o "Registro", cada campo vira um widget novo e renasce com o
    # valor do registro escolhido (o Streamlit prioriza o session_state sobre
    # `value`/`index` quando a key não muda). Palavras-chave e os pickers de
    # tabela têm sincronização própria (`_sync_*`).
    record_key = str(cur.get("id")) if editing else "novo"
    rk = record_key
    _sync_keywords_state(kp, record_key, cur.get("palavras_chave") or "")

    # Todos os widgets ficam fora de st.form por causa do picker de tabelas/
    # colunas do indicador, que precisa recarregar a cada escolha de
    # catalog/schema/table (igual select_object/render_editor).
    power_steward = ""
    if is_indicador:
        power_steward = _render_power_steward_select(cur.get("power_steward") or "", f"ind_ps_{rk}")

    dom_ids = [d["id"] for d in doms]
    dom_options = [None] + dom_ids
    cur_dom = cur.get("dominio_id")
    dom_idx = dom_options.index(cur_dom) if editing and cur_dom in dom_options else 0
    dom_id = st.selectbox(
        "Domínio de dados", options=dom_options, index=dom_idx,
        format_func=lambda i: "(nenhum)" if i is None else dom_nome.get(i, i), key=f"{kp}_dom_{rk}",
    )
    sub_ids_all = [s["id"] for s in subs if s["dominio_id"] == dom_id] if dom_id is not None else []
    sub_options = [None] + sub_ids_all
    cur_sub = cur.get("subdominio_id")
    sub_idx = sub_options.index(cur_sub) if editing and cur_sub in sub_options else 0
    sub_id = st.selectbox(
        "Sub-domínio", options=sub_options, index=sub_idx, key=f"{kp}_sub_{rk}",
        format_func=lambda i: "(nenhum)" if i is None else sub_nome.get(i, i),
    )

    data_owner = _select_pessoa_cadastrada(
        "Data owner", "Owner", stewards, dom_id, sub_id, cur.get("data_owner") or "", f"{kp}_owner_{rk}",
    )
    data_steward = _select_pessoa_cadastrada(
        "Data steward", "Steward", stewards, dom_id, sub_id, cur.get("data_steward") or "", f"{kp}_steward_{rk}",
    )

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        nome = st.text_input(
            "Nome do indicador *" if is_indicador else "Nome do termo *",
            value=cur["nome"] or "", key=f"{kp}_nome_{rk}",
        )
        macroprocesso = st.text_input("Macroprocesso", value=cur.get("macroprocesso") or "", key=f"{kp}_macro_{rk}")
    with c2:
        st.text_input(
            "Palavras-chave", key=f"{kp}_kw_input",
            placeholder="digite uma palavra e tecle Enter",
            on_change=_add_keyword, args=(kp,),
        )
    kw_list = _render_keyword_chips(kp)
    objetivo = st.text_area(
        "Objetivo" if is_indicador else "Definição",
        value=cur.get("objetivo") or "", key=f"{kp}_obj_{rk}",
    )

    rotulo_seguranca = rotulo_privacidade = observacoes = ""
    variaveis_utilizadas = memoria_calculo = restricoes = unidade = nivel_apuracao = ""
    dim_items: list[dict] = []
    met_items: list[dict] = []
    if is_indicador:
        st.markdown("##### Classificação")
        c3, c4 = st.columns(2)
        with c3:
            rotulo_seguranca = st.selectbox(
                "Rótulo de segurança", options=seguranca_opts,
                index=seguranca_opts.index(cur["rotulo_seguranca"]) if cur.get("rotulo_seguranca") in seguranca_opts else 0,
                key=f"{kp}_seg_{rk}",
            )
        with c4:
            rotulo_privacidade = st.selectbox(
                "Rótulo de privacidade", options=privacidade_opts,
                index=privacidade_opts.index(cur["rotulo_privacidade"]) if cur.get("rotulo_privacidade") in privacidade_opts else 0,
                key=f"{kp}_priv_{rk}",
            )

        st.markdown("##### Indicador")
        c5, c6 = st.columns(2)
        with c5:
            default_unidade = cur.get("unidade") or ""
            unidade_opts = _UNIDADE_OPTIONS if default_unidade in _UNIDADE_OPTIONS else [default_unidade] + _UNIDADE_OPTIONS
            unidade_sel = st.selectbox(
                "Unidade", options=unidade_opts, index=unidade_opts.index(default_unidade), key=f"term_unidade_{rk}",
            )
            if unidade_sel == "Outra…":
                unidade = st.text_input(
                    "Unidade (digite)",
                    value="" if default_unidade in _UNIDADE_OPTIONS else default_unidade,
                    key=f"term_unidade_custom_{rk}",
                )
            else:
                unidade = unidade_sel
        with c6:
            nivel_apuracao = st.selectbox(
                "Nível de apuração", options=_NIVEL_APURACAO_OPTIONS,
                index=_NIVEL_APURACAO_OPTIONS.index(cur["nivel_apuracao"]) if cur.get("nivel_apuracao") in _NIVEL_APURACAO_OPTIONS else 0,
                key=f"term_nivel_{rk}",
            )
        variaveis_utilizadas = st.text_area("Variáveis utilizadas", value=cur.get("variaveis_utilizadas") or "", key=f"term_vars_{rk}")
        restricoes = st.text_area("Restrições", value=cur.get("restricoes") or "", key=f"term_restr_{rk}")

        _sync_tabela_picker_state("dim", record_key, _parse_tabelas_json(cur.get("dimensao_tabelas")))
        _sync_tabela_picker_state("met", record_key, _parse_tabelas_json(cur.get("metrica_tabelas")))

        st.markdown("###### Dimensão — tabelas e colunas que compõem a dimensão")
        dim_items = _render_tabela_picker(user, "dim")
        st.markdown("###### Métrica — tabelas e colunas que formam a métrica")
        met_items = _render_tabela_picker(user, "met")

        memoria_calculo = st.text_area("Memória de cálculo (fórmula)", value=cur.get("memoria_calculo") or "", key=f"term_memoria_{rk}")
        observacoes = st.text_area("Observações", value=cur.get("observacoes") or "", key=f"{kp}_obs_{rk}")

    rotulo_item = "indicador" if is_indicador else "termo"
    st.divider()
    saved = st.button("💾 Salvar", type="primary", key=f"{kp}_save")

    if saved:
        nome = (nome or "").strip()
        if not nome:
            st.warning(f"Informe o nome do {rotulo_item}.")
            return
        palavras_chave = ", ".join(kw_list)
        dom_sql = "NULL" if dom_id is None else str(int(dom_id))
        sub_sql = "NULL" if sub_id is None else str(int(sub_id))
        values = dict(
            tipo=tipo, nome=nome, objetivo=objetivo,
            palavras_chave=palavras_chave, macroprocesso=macroprocesso,
            data_owner=data_owner, data_steward=data_steward,
        )
        if is_indicador:
            values.update(
                power_steward=power_steward,
                observacoes=observacoes,
                rotulo_seguranca=rotulo_seguranca, rotulo_privacidade=rotulo_privacidade,
                nivel_apuracao=nivel_apuracao, unidade=unidade,
                variaveis_utilizadas=variaveis_utilizadas, memoria_calculo=memoria_calculo,
                restricoes=restricoes,
                dimensao_tabelas=_dump_tabelas_json(dim_items),
                metrica_tabelas=_dump_tabelas_json(met_items),
            )
        if editing:
            set_clause = ", ".join(f"{col} = {q_str(val)}" for col, val in values.items())
            run_exec(
                f"UPDATE {_ont(ont_table)} SET {set_clause}, "
                f"dominio_id = {dom_sql}, subdominio_id = {sub_sql}, "
                f"atualizado_em = current_timestamp(), atualizado_por = {q_str(user)} "
                f"WHERE id = {int(cur['id'])}"
            )
        else:
            cols = ["dominio_id", "subdominio_id", *values.keys(), "criado_em", "criado_por"]
            vals_sql = [dom_sql, sub_sql, *[q_str(v) for v in values.values()], "current_timestamp()", q_str(user)]
            run_exec(
                f"INSERT INTO {_ont(ont_table)} ({', '.join(cols)}) "
                f"VALUES ({', '.join(vals_sql)})"
            )
        # Limpa o estado do picker de tabelas pra não vazar seleção entre
        # registros diferentes no próximo rerun (_finish_write já chama rerun).
        for kind in ("dim", "met"):
            st.session_state.pop(f"term_{kind}_items", None)
            st.session_state.pop(f"term_{kind}_items_for", None)
        st.session_state.pop(_kw_list_key(kp), None)
        st.session_state.pop(f"{kp}_kw_for", None)
        # Se foi um "novo", zera os campos (keys sufixadas com `_novo`).
        if not editing:
            for suf in ("dom", "sub", "owner_sel", "owner_txt", "steward_sel",
                        "steward_txt", "nome", "macro", "obj", "seg", "priv", "obs"):
                st.session_state.pop(f"{kp}_{suf}_novo", None)
            for base in ("ind_ps", "term_unidade", "term_unidade_custom",
                         "term_nivel", "term_vars", "term_restr", "term_memoria"):
                st.session_state.pop(f"{base}_novo", None)
        _finish_write(f"{'Indicador' if is_indicador else 'Termo de negócio'} salvo.")

    if editing:
        st.divider()
        st.markdown("#### Excluir")
        if st.button(f"🗑️ Excluir {rotulo_item} '{cur['nome']}'", key=f"{kp}_del"):
            run_exec(f"DELETE FROM {_ont(ont_table)} WHERE id = {int(cur['id'])}")
            _finish_write(f"{'Indicador' if is_indicador else 'Termo de negócio'} excluído.")


def page_glossario_negocio() -> None:
    _render_glossario_editor(
        is_indicador=False, ont_table="glossario_negocio",
        list_fn=list_glossario_negocio, titulo="Glossário de Negócio", icone="📖",
    )


def page_indicadores() -> None:
    _render_glossario_editor(
        is_indicador=True, ont_table="indicadores",
        list_fn=list_indicadores, titulo="Indicador", icone="📈",
    )


def _render_termo_detalhe(cur: dict, dom_nome: dict, sub_nome: dict) -> None:
    """Card de detalhe de um termo/indicador — somente leitura."""
    is_indicador = cur.get("tipo") == "Indicador"
    dom = dom_nome.get(cur.get("dominio_id"), "—") if pd.notna(cur.get("dominio_id")) else "—"
    sub = sub_nome.get(cur.get("subdominio_id"), "—") if pd.notna(cur.get("subdominio_id")) else "—"

    st.markdown(f"### {'📈' if is_indicador else '📖'} {cur['nome']}")
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Tipo**\n\n{cur.get('tipo') or '—'}")
    c2.markdown(f"**Domínio**\n\n{dom}")
    c3.markdown(f"**Sub-domínio**\n\n{sub}")
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Data Owner**\n\n{cur.get('data_owner') or '—'}")
    c2.markdown(f"**Data Steward**\n\n{cur.get('data_steward') or '—'}")
    c3.markdown(f"**Macroprocesso**\n\n{cur.get('macroprocesso') or '—'}")

    if cur.get("palavras_chave"):
        st.markdown(f"**Palavras-chave:** {cur['palavras_chave']}")
    if cur.get("objetivo"):
        st.markdown("**Objetivo**" if is_indicador else "**Definição**")
        st.write(cur["objetivo"])

    if is_indicador:
        st.markdown(
            f"**Rótulo de segurança:** {cur.get('rotulo_seguranca') or '—'}  |  "
            f"**Rótulo de privacidade:** {cur.get('rotulo_privacidade') or '—'}"
        )
        st.markdown("#### Indicador")
        ps_email = cur.get("power_steward") or ""
        if ps_email:
            try:
                nm = {u["email"].lower(): u["nome"] for u in list_users_for_search()}.get(ps_email.lower())
            except Exception:
                nm = None
            ps_txt = f"{nm} <{ps_email}>" if nm and nm.lower() != ps_email.lower() else ps_email
            st.markdown(f"**Power Steward:** {ps_txt}")
        c1, c2 = st.columns(2)
        c1.markdown(f"**Unidade**\n\n{cur.get('unidade') or '—'}")
        c2.markdown(f"**Nível de apuração**\n\n{cur.get('nivel_apuracao') or '—'}")
        if cur.get("variaveis_utilizadas"):
            st.markdown("**Variáveis utilizadas**")
            st.write(cur["variaveis_utilizadas"])
        if cur.get("memoria_calculo"):
            st.markdown("**Memória de cálculo (fórmula)**")
            st.write(cur["memoria_calculo"])
        if cur.get("restricoes"):
            st.markdown("**Restrições**")
            st.write(cur["restricoes"])
        for titulo, campo in (("Dimensão", "dimensao_tabelas"), ("Métrica", "metrica_tabelas")):
            items = _parse_tabelas_json(cur.get(campo))
            if items:
                st.markdown(f"**{titulo} — tabelas e colunas**")
                for it in items:
                    cols_txt = ", ".join(it.get("colunas") or []) or "(tabela inteira)"
                    st.caption(f'`{it["catalogo"]}.{it["schema"]}.{it["tabela"]}` — {cols_txt}')

    if cur.get("observacoes"):
        st.markdown("**Observações**")
        st.write(cur["observacoes"])


def page_consulta_termos() -> None:
    st.title("📚 Glossário de Termos de Negócio")
    st.caption(
        "Consulta aberta ao glossário de termos de negócio e indicadores. Use a "
        "busca e os filtros para localizar um termo; os detalhes aparecem abaixo."
    )

    termos = list_termos_negocio()
    if termos.empty:
        st.info("Nenhum termo de negócio cadastrado ainda.")
        return

    doms = list_dominios().to_dict("records")
    subs = list_subdominios().to_dict("records")
    dom_nome = {d["id"]: d["nome"] for d in doms}
    sub_nome = {s["id"]: s["nome"] for s in subs}

    df = termos.copy()
    df["Domínio"] = df["dominio_id"].map(lambda i: dom_nome.get(i, "—") if pd.notna(i) else "—")
    df["Sub-domínio"] = df["subdominio_id"].map(lambda i: sub_nome.get(i, "—") if pd.notna(i) else "—")

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        busca = st.text_input(
            "Buscar", placeholder="nome, palavra-chave, definição/objetivo…", key="cons_busca",
        )
    with c2:
        tipo_f = st.selectbox("Tipo", options=["(todos)"] + _TERMO_TIPO_OPTIONS, key="cons_tipo")
    with c3:
        dom_opts = ["(todos)"] + sorted({d for d in df["Domínio"] if d != "—"})
        dom_f = st.selectbox("Domínio", options=dom_opts, key="cons_dom")

    view = df
    if busca:
        q = busca.strip().lower()
        view = view[
            view["nome"].fillna("").str.lower().str.contains(q, regex=False)
            | view["palavras_chave"].fillna("").str.lower().str.contains(q, regex=False)
            | view["objetivo"].fillna("").str.lower().str.contains(q, regex=False)
        ]
    if tipo_f != "(todos)":
        view = view[view["tipo"] == tipo_f]
    if dom_f != "(todos)":
        view = view[view["Domínio"] == dom_f]

    st.caption(f"{len(view)} termo(s) encontrado(s).")
    st.dataframe(
        view.rename(columns={
            "tipo": "Tipo", "nome": "Nome", "data_owner": "Data Owner",
            "nivel_apuracao": "Nível de Apuração",
        })[["Tipo", "Nome", "Domínio", "Sub-domínio", "Data Owner", "Nível de Apuração"]],
        use_container_width=True, hide_index=True,
    )

    if view.empty:
        return

    st.divider()
    recs = view.to_dict("records")
    opts = [f'{r["nome"]}  ·  {r["tipo"]}  (id {r["id"]})' for r in recs]
    sel = st.selectbox("Ver detalhes do termo", options=opts, key="cons_sel")
    _render_termo_detalhe(recs[opts.index(sel)], dom_nome, sub_nome)


def page_permissoes() -> None:
    st.title("🔒 Usuários & Permissões")
    st.caption(
        "Cadastro de usuários (espelho do workspace) e seu permissionamento. "
        "**Papel** define o que edita nos cadastros (admin/editor/leitor). As "
        "**checkboxes** liberam a visualização/ação por usuário: *Ver cadastros* mostra "
        "o menu Cadastros; *Ver logs* mostra o menu Auditoria; *Aprovador de tags* libera "
        "o backlog de aprovação de tagueamento; *Power Steward* faz o usuário aparecer no "
        "campo Power Steward da tela Indicador. **Admin enxerga/faz tudo** independentemente "
        "das checkboxes. Só admins acessam esta tela."
    )
    _show_cad_feedback()
    user = st.session_state.get("user", "")

    df = list_permissoes()
    st.dataframe(
        df.rename(columns={
            "id": "ID", "nome": "Nome", "email": "E-mail", "papel": "Papel",
            "ver_cadastros": "Ver cadastros", "ver_logs": "Ver logs",
            "aprovador_tags": "Aprovador de tags", "power_steward": "Power Steward",
        }),
        use_container_width=True, hide_index=True,
    )
    recs = df.to_dict("records")

    # ---- Adicionar (com busca de usuário, igual ao Data Stewards) ----
    st.divider()
    st.markdown("#### Adicionar")
    users = list_users_for_search()
    email = None
    nome_sugerido = ""
    manual = st.toggle(
        "✍️ Informar manualmente (usuário não encontrado na busca)",
        value=not users, disabled=not users, key="perm_manual",
    )
    if users and not manual:
        term = st.text_input("🔍 Buscar usuário (nome ou e-mail)", key="perm_search")
        if term:
            t = term.lower()
            matches = [u for u in users if t in u["nome"].lower() or t in u["email"].lower()][:50]
            if matches:
                pick = st.selectbox(
                    "Resultado", options=matches,
                    format_func=lambda u: f'{u["nome"]} <{u["email"]}>', key="perm_pick",
                )
                email = pick["email"]
                _sc = str(pick.get("nome") or "").strip()
                nome_sugerido = _sc if _sc.lower() != email.lower() else ""
                st.text_input("E-mail", value=email, disabled=True, key="perm_email_view")
            else:
                st.caption(
                    "Nenhum usuário encontrado — ative *Informar manualmente* acima "
                    "para digitar o e-mail."
                )
    else:
        if not users:
            st.caption("Não foi possível listar usuários (workspace/conta) — informe manualmente.")
        email = st.text_input("E-mail corporativo *", key="perm_email_manual")

    nome_key = "perm_nome_add_" + (email if (email and users and not manual) else "manual")
    nome_add = st.text_input(
        "Nome (para exibição)", value=nome_sugerido, key=nome_key,
        placeholder="ex.: Luciano Zani — usado na saudação da tela de Início",
    )
    papel_add = st.selectbox("Papel *", options=["admin", "editor", "leitor"], key="perm_papel_add")
    ca, cb, cc, cd = st.columns(4)
    with ca:
        add_ver_cad = st.checkbox("Ver cadastros", value=True, key="perm_add_ver_cad")
    with cb:
        add_ver_log = st.checkbox("Ver logs", value=False, key="perm_add_ver_log")
    with cc:
        add_aprov = st.checkbox("Aprovador de tags", value=False, key="perm_add_aprov")
    with cd:
        add_power = st.checkbox("Power Steward", value=False, key="perm_add_power")
    st.caption("Admin ignora as checkboxes (vê/faz tudo). *Power Steward* é só um "
               "rótulo — não muda o que o usuário enxerga/faz.")
    if st.button("💾 Adicionar usuário", type="primary"):
        em = (email or "").strip().lower()
        if "@" not in em:
            st.warning("Selecione/informe um usuário com e-mail válido.")
            return
        # Checagem com query fresca (não a lista em cache) — evita duplicar o
        # mesmo usuário. O INSERT atômico abaixo é o guard definitivo.
        if _count(f"SELECT count(*) FROM {_cad('permissoes')} WHERE lower(email) = {q_str(em)}"):
            st.error("Esse usuário já tem permissão — edite na seção abaixo.")
            return
        run_exec(
            f"INSERT INTO {_cad('permissoes')} "
            f"(nome, email, papel, ver_cadastros, ver_logs, aprovador_tags, power_steward, criado_em, criado_por) "
            f"SELECT {q_str((nome_add or '').strip())}, {q_str(em)}, {q_str(papel_add)}, "
            f"{str(add_ver_cad).lower()}, {str(add_ver_log).lower()}, {str(add_aprov).lower()}, "
            f"{str(add_power).lower()}, current_timestamp(), {q_str(user)} "
            f"FROM (SELECT 1) WHERE NOT EXISTS "
            f"(SELECT 1 FROM {_cad('permissoes')} WHERE lower(email) = {q_str(em)})"
        )
        _finish_write("Usuário adicionado.")

    # ---- Editar / excluir (registros existentes) ----
    if recs:
        st.divider()
        st.markdown("#### Editar / excluir")
        opts = [f'{r["email"]} ({r["papel"]})' for r in recs]
        sel = st.selectbox("Registro", options=opts, key="perm_edit_sel")
        cur = recs[opts.index(sel)]
        rid = int(cur["id"])
        ed_nome = st.text_input("Nome (para exibição)", value=cur.get("nome") or "",
                                key=f"perm_nome_edit_{rid}", placeholder="ex.: Luciano Zani")
        papeis = ["admin", "editor", "leitor"]
        novo = st.selectbox(
            "Papel", options=papeis,
            index=papeis.index((cur.get("papel") or "leitor").lower())
            if (cur.get("papel") or "leitor").lower() in papeis else 2,
            key=f"perm_papel_edit_{rid}",
        )
        e1, e2, e3, e4 = st.columns(4)
        with e1:
            ed_ver_cad = st.checkbox(
                "Ver cadastros", value=_as_bool(cur.get("ver_cadastros")), key=f"perm_edit_ver_cad_{rid}")
        with e2:
            ed_ver_log = st.checkbox(
                "Ver logs", value=_as_bool(cur.get("ver_logs")), key=f"perm_edit_ver_log_{rid}")
        with e3:
            ed_aprov = st.checkbox(
                "Aprovador de tags", value=_as_bool(cur.get("aprovador_tags")), key=f"perm_edit_aprov_{rid}")
        with e4:
            ed_power = st.checkbox(
                "Power Steward", value=_as_bool(cur.get("power_steward")), key=f"perm_edit_power_{rid}")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Salvar"):
                run_exec(
                    f"UPDATE {_cad('permissoes')} SET nome = {q_str((ed_nome or '').strip())}, "
                    f"papel = {q_str(novo)}, "
                    f"ver_cadastros = {str(ed_ver_cad).lower()}, ver_logs = {str(ed_ver_log).lower()}, "
                    f"aprovador_tags = {str(ed_aprov).lower()}, power_steward = {str(ed_power).lower()}, "
                    f"atualizado_em = current_timestamp(), atualizado_por = {q_str(user)} "
                    f"WHERE id = {rid}"
                )
                _finish_write("Usuário atualizado.")
        with c2:
            if st.button("🗑️ Excluir usuário"):
                # Não deixa remover o último admin.
                if (cur.get("papel") or "").lower() == "admin" and _count(
                    f"SELECT count(*) FROM {_cad('permissoes')} WHERE lower(papel) = 'admin'"
                ) <= 1:
                    st.error("Não é possível remover o último admin.")
                else:
                    run_exec(f"DELETE FROM {_cad('permissoes')} WHERE id = {int(cur['id'])}")
                    _finish_write("Usuário excluído.")


def page_log_comentarios() -> None:
    st.title("📜 Log de comentários")
    st.caption(
        "Auditoria de quem alterou comentários de tabela/coluna. Como a escrita "
        "roda via Service Principal, o Unity Catalog não guarda o autor real — "
        "aqui fica registrado o usuário logado no app."
    )
    try:
        df = list_log_comentarios()
    except Exception as exc:
        st.warning(f"Não foi possível ler o log: {exc}")
        return
    if df.empty:
        st.info("Ainda não há alterações de comentário registradas.")
        return

    # Filtros simples (usuário / ação).
    c1, c2 = st.columns(2)
    with c1:
        termo = st.text_input("🔍 Filtrar por usuário", key="log_user").strip().lower()
    with c2:
        acao = st.selectbox("Ação", options=["(todas)", "inserir", "alterar", "remover"], key="log_acao")
    view = df
    if termo:
        view = view[view["usuario"].str.lower().str.contains(termo, na=False)]
    if acao != "(todas)":
        view = view[view["acao"] == acao]

    st.caption(f"{len(view)} de {len(df)} registro(s).")
    st.dataframe(
        view.rename(columns={
            "criado_em": "Quando", "usuario": "Usuário", "acao": "Ação", "objeto": "Objeto",
            "catalogo": "Catálogo", "db_schema": "Schema", "tabela": "Tabela", "coluna": "Coluna",
            "comentario_anterior": "Comentário anterior", "comentario_novo": "Comentário novo",
            "ambiente": "Ambiente",
        }),
        use_container_width=True, hide_index=True,
    )


def page_log_tags() -> None:
    st.title("🏷️ Log de tags")
    st.caption(
        "Auditoria de quem aplicou/alterou/removeu tags governadas nas colunas. "
        "As tags rodam com o token do usuário (OBO); aqui fica o rastro de quem fez."
    )
    try:
        df = list_log_tags()
    except Exception as exc:
        st.warning(f"Não foi possível ler o log: {exc}")
        return
    if df.empty:
        st.info("Ainda não há alterações de tag registradas.")
        return

    c1, c2 = st.columns(2)
    with c1:
        termo = st.text_input("🔍 Filtrar por usuário", key="logtag_user").strip().lower()
    with c2:
        acao = st.selectbox("Ação", options=["(todas)", "aplicar", "alterar", "remover"], key="logtag_acao")
    view = df
    if termo:
        view = view[view["usuario"].str.lower().str.contains(termo, na=False)]
    if acao != "(todas)":
        view = view[view["acao"] == acao]

    st.caption(f"{len(view)} de {len(df)} registro(s).")
    st.dataframe(
        view.rename(columns={
            "criado_em": "Quando", "usuario": "Usuário", "acao": "Ação",
            "catalogo": "Catálogo", "db_schema": "Schema", "tabela": "Tabela", "coluna": "Coluna",
            "tag_chave": "Tag", "valor_anterior": "Valor anterior", "valor_novo": "Valor novo",
            "ambiente": "Ambiente",
        }),
        use_container_width=True, hide_index=True,
    )


def page_relatorio_auditoria() -> None:
    st.title("📋 Relatório de Auditoria")
    st.caption(
        "Visão consolidada de comentários e tags alterados em tabelas/colunas do "
        "Unity Catalog — para a governança revisar o que foi documentado."
    )
    try:
        com = list_log_comentarios()
        tags = list_log_tags()
    except Exception as exc:
        st.warning(f"Não foi possível ler os logs: {exc}")
        return
    if com.empty and tags.empty:
        st.info("Ainda não há comentários ou tags registrados.")
        return

    rows = []
    for r in com.to_dict("records"):
        rows.append({
            "criado_em": r["criado_em"], "tipo": "Comentário", "usuario": r["usuario"],
            "acao": r["acao"], "catalogo": r["catalogo"], "db_schema": r["db_schema"],
            "tabela": r["tabela"], "coluna": r.get("coluna"),
            "detalhe": f"{(r.get('comentario_anterior') or '(vazio)')[:60]} → "
                       f"{(r.get('comentario_novo') or '(vazio)')[:60]}",
        })
    for r in tags.to_dict("records"):
        rows.append({
            "criado_em": r["criado_em"], "tipo": "Tag", "usuario": r["usuario"],
            "acao": r["acao"], "catalogo": r["catalogo"], "db_schema": r["db_schema"],
            "tabela": r["tabela"], "coluna": r.get("coluna"),
            "detalhe": f"{r['tag_chave']}: {r.get('valor_anterior') or '(vazio)'} → "
                       f"{r.get('valor_novo') or '(vazio)'}",
        })
    df = pd.DataFrame(rows).sort_values("criado_em", ascending=False)

    c1, c2, c3 = st.columns(3)
    with c1:
        termo = st.text_input("🔍 Filtrar por usuário", key="rel_user").strip().lower()
    with c2:
        tipo = st.selectbox("Tipo", options=["(todos)", "Comentário", "Tag"], key="rel_tipo")
    with c3:
        tabela_termo = st.text_input("🔍 Filtrar por tabela", key="rel_tabela").strip().lower()

    view = df
    if termo:
        view = view[view["usuario"].str.lower().str.contains(termo, na=False)]
    if tipo != "(todos)":
        view = view[view["tipo"] == tipo]
    if tabela_termo:
        view = view[view["tabela"].str.lower().str.contains(tabela_termo, na=False)]

    st.caption(f"{len(view)} de {len(df)} registro(s).")
    st.dataframe(
        view.rename(columns={
            "criado_em": "Quando", "tipo": "Tipo", "usuario": "Usuário", "acao": "Ação",
            "catalogo": "Catálogo", "db_schema": "Schema", "tabela": "Tabela", "coluna": "Coluna",
            "detalhe": "Detalhe",
        })[["Quando", "Tipo", "Usuário", "Ação", "Catálogo", "Schema", "Tabela", "Coluna", "Detalhe"]],
        use_container_width=True, hide_index=True,
    )


def _decidir_backlog(item: dict, status: str, aprovador: str, motivo_decisao: str) -> None:
    """Aprova (aplica a tag de fato) ou rejeita um item do backlog."""
    if status == "aprovado":
        catalog, schema, table, column = item["catalogo"], item["db_schema"], item["tabela"], item["coluna"]
        full = q_full(catalog, schema, table)
        col_q = q_ident(column)
        try:
            if item["acao"] == "remover":
                sql = f"ALTER TABLE {full} ALTER COLUMN {col_q} UNSET TAGS ({q_str(item['tag_chave'])})"
            else:
                sql = (
                    f"ALTER TABLE {full} ALTER COLUMN {col_q} "
                    f"SET TAGS ({q_str(item['tag_chave'])} = {q_str(item['valor_novo'])})"
                )
            run_exec(sql, prefer_user=True)
            _log_tag_change(
                item["solicitante"], catalog, schema, table, column, item["acao"],
                item["tag_chave"], item.get("valor_anterior"), item.get("valor_novo"),
            )
        except Exception as exc:
            st.error(f"Falha ao aplicar a tag aprovada: {exc}")
            return
    run_exec(
        f"UPDATE {_cad('tag_backlog')} SET status = {q_str(status)}, aprovador = {q_str(aprovador)}, "
        f"decidido_em = current_timestamp(), motivo_decisao = {q_str(motivo_decisao or '')} "
        f"WHERE id = {int(item['id'])}"
    )
    try:
        list_tag_backlog.clear()
        get_applied_column_tags.clear()
    except Exception:
        pass
    st.session_state["cad_feedback"] = (
        "success",
        "✅ Item aprovado e tag aplicada." if status == "aprovado" else "🚫 Item rejeitado.",
    )
    st.rerun()


def page_tag_backlog() -> None:
    st.title("✅ Backlog de Aprovação de Tags")
    st.caption(
        "Tentativas de tagueamento em colunas de dado pessoal que não cumpriram a "
        "regra (privacidade=dado pessoal + seguranca=confidencial) ficam aqui até um "
        "aprovador decidir. **Aprovar** aplica a tag de fato no Unity Catalog; "
        "**rejeitar** descarta a tentativa sem aplicar nada."
    )
    _show_cad_feedback()
    user = st.session_state.get("user", "")

    try:
        pend = list_tag_backlog("pendente")
    except Exception as exc:
        st.warning(f"Não foi possível ler o backlog: {exc}")
        return

    if pend.empty:
        st.success("Nenhum item pendente. 🎉")
    else:
        st.dataframe(
            pend.rename(columns={
                "criado_em": "Solicitado em", "solicitante": "Solicitante", "catalogo": "Catálogo",
                "db_schema": "Schema", "tabela": "Tabela", "coluna": "Coluna", "tag_chave": "Tag",
                "valor_anterior": "Valor anterior", "valor_novo": "Valor solicitado", "acao": "Ação",
                "motivo": "Motivo",
            })[["Solicitado em", "Solicitante", "Catálogo", "Schema", "Tabela", "Coluna",
                "Tag", "Ação", "Valor anterior", "Valor solicitado", "Motivo"]],
            use_container_width=True, hide_index=True,
        )

        st.divider()
        st.markdown("#### Decidir item")
        recs = pend.to_dict("records")
        opts = [
            f'#{r["id"]} — {r["tabela"]}.{r["coluna"]} — {r["tag_chave"]}='
            f'{r["valor_novo"] or "(remover)"} ({r["solicitante"]})'
            for r in recs
        ]
        sel = st.selectbox("Item", options=opts, key="backlog_sel")
        cur = recs[opts.index(sel)]
        motivo_decisao = st.text_input("Comentário da decisão (opcional)", key="backlog_motivo")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ Aprovar e aplicar", type="primary"):
                _decidir_backlog(cur, "aprovado", user, motivo_decisao)
        with c2:
            if st.button("❌ Rejeitar"):
                _decidir_backlog(cur, "rejeitado", user, motivo_decisao)

    st.divider()
    with st.expander("Histórico de decisões"):
        try:
            hist = list_tag_backlog()
            hist = hist[hist["status"] != "pendente"]
        except Exception:
            hist = pd.DataFrame()
        if hist.empty:
            st.caption("Nenhuma decisão registrada ainda.")
        else:
            st.dataframe(
                hist.rename(columns={
                    "criado_em": "Solicitado em", "solicitante": "Solicitante", "tabela": "Tabela",
                    "coluna": "Coluna", "tag_chave": "Tag", "valor_novo": "Valor solicitado",
                    "status": "Status", "aprovador": "Decidido por", "decidido_em": "Decidido em",
                    "motivo_decisao": "Comentário",
                })[["Solicitado em", "Solicitante", "Tabela", "Coluna", "Tag", "Valor solicitado",
                    "Status", "Decidido por", "Decidido em", "Comentário"]],
                use_container_width=True, hide_index=True,
            )


def make_dashboard_page(row: dict):
    """Fábrica de página para um dashboard cadastrado (um `st.Page` por linha)."""

    def _page() -> None:
        st.title(f"{row.get('icone') or '📊'} {row['nome']}")
        if row.get("descricao"):
            st.caption(row["descricao"])
        st.caption(
            "Não é possível embutir o dashboard diretamente nesta página — o "
            "navegador bloqueia o cookie de sessão do workspace dentro de um "
            "iframe de outra origem —, então ele abre em uma nova aba, já "
            "autenticado com a sua sessão atual."
        )
        st.link_button("↗️ Abrir dashboard", row["url"], type="primary")

    return _page


def user_visible_dashboards(user: str, is_admin: bool) -> list[dict]:
    """Dashboards ativos que o usuário pode ver: admin vê todos; steward vê os do

    seu domínio (e, se o dashboard restringir a um sub-domínio, só se o steward
    for daquele sub-domínio específico).
    """
    dash = list_dashboards()
    if dash.empty:
        return []
    ativos = [r for r in dash.to_dict("records") if r.get("ativo", True)]
    if is_admin:
        return ativos
    stw = list_stewards()
    stw = stw[stw["tipo"] == "Steward"] if not stw.empty else stw
    minhas = stw[stw["email"].str.lower() == (user or "").lower()] if not stw.empty else stw
    dom_ids = set(minhas["dominio_id"].tolist()) if not minhas.empty else set()
    sub_ids = set(minhas["subdominio_id"].tolist()) if not minhas.empty else set()
    visiveis = []
    for r in ativos:
        if r["dominio_id"] not in dom_ids:
            continue
        if r.get("subdominio_id") is not None and pd.notna(r["subdominio_id"]):
            if int(r["subdominio_id"]) not in sub_ids:
                continue
        visiveis.append(r)
    return visiveis


# ---------------------------------------------------------------------------
# Tela de início — painel adaptado por papel/flags
# ---------------------------------------------------------------------------

_INICIO_CSS = """
<style>
.inicio-chip {
    display: inline-block; font-size: 12px; font-weight: 500;
    padding: 2px 9px; margin: 0 4px 4px 0; border-radius: 999px;
    background: #eef0fb; color: #3b4aa0;
}
.inicio-chip.leitor { background: #f0f2f6; color: #5c6575; }
/* destaque âmbar no card de pendências */
.st-key-inicio_pend { border-left: 3px solid #d98324 !important; }
</style>
"""


@st.cache_data(ttl=60, show_spinner=False)
def _novos_na_semana() -> dict:
    """Quantos registros de cada cadastro foram criados nos últimos 7 dias —
    para os `delta` dos `st.metric` da tela de início."""
    wk = "criado_em >= current_timestamp() - INTERVAL 7 DAYS"
    try:
        return {
            "dominios": _count(f"SELECT count(*) FROM {_cad('dominios')} WHERE {wk}"),
            "subdominios": _count(f"SELECT count(*) FROM {_cad('subdominios')} WHERE {wk}"),
            "stewards": _count(f"SELECT count(*) FROM {_cad('data_stewards')} WHERE {wk}"),
            "termos": _count(f"SELECT count(*) FROM {_ont('glossario_negocio')} WHERE {wk}"),
            "indicadores": _count(f"SELECT count(*) FROM {_ont('indicadores')} WHERE {wk}"),
        }
    except Exception:
        return {}


def _lacunas_cadastro(doms, subs, stew, glo, ind) -> list[tuple]:
    """(contagem, texto, chave_nav) das lacunas de cadastro — tudo em memória."""
    stw = stew[stew["tipo"] == "Steward"] if not stew.empty else stew
    dom_ok = set(stw["dominio_id"].dropna().tolist()) if not stw.empty else set()
    sub_ok = set(stw["subdominio_id"].dropna().tolist()) if not stw.empty else set()
    out: list[tuple] = []
    n = sum(1 for d in doms.to_dict("records") if d["id"] not in dom_ok)
    if n:
        out.append((n, f"{n} domínio(s) sem Data Steward", "stewards"))
    n = sum(1 for s in subs.to_dict("records") if s["id"] not in sub_ok)
    if n:
        out.append((n, f"{n} sub-domínio(s) sem Data Steward", "stewards"))
    n = sum(1 for r in ind.to_dict("records") if not str(r.get("power_steward") or "").strip())
    if n:
        out.append((n, f"{n} indicador(es) sem Power Steward", "indicadores"))
    n = sum(1 for r in glo.to_dict("records") if not str(r.get("objetivo") or "").strip())
    if n:
        out.append((n, f"{n} termo(s) sem definição", "glossario_edit"))
    n = sum(1 for r in ind.to_dict("records") + glo.to_dict("records") if not pd.notna(r.get("dominio_id")))
    if n:
        out.append((n, f"{n} termo(s)/indicador(es) sem domínio", None))
    return out


def _meus_indicadores(ind, email: str) -> list[dict]:
    if ind.empty:
        return []
    e = (email or "").lower()
    return [r for r in ind.to_dict("records") if str(r.get("power_steward") or "").lower() == e]


def _fmt_ts(v) -> str:
    ts = pd.to_datetime(v, errors="coerce")
    return ts.strftime("%d/%m %H:%M") if pd.notna(ts) else str(v)[:16]


_ACAO_PART = {
    "aplicar": "aplicada", "inserir": "adicionado", "alterar": "alterado",
    "remover": "removida", "aprovar": "aprovada", "rejeitar": "rejeitada",
}


def _atividade_recente(limit: int = 8) -> tuple[list[str], int]:
    """Últimas alterações de comentário + tag (texto pronto) e a contagem da
    semana."""
    try:
        lc = list_log_comentarios(40)
        lt = list_log_tags(40)
    except Exception:
        return [], 0

    def _obj(r) -> str:
        return ".".join(
            str(x) for x in (r.get("catalogo"), r.get("db_schema"), r.get("tabela"), r.get("coluna")) if x
        )

    def _quem(r) -> str:
        return str(r.get("usuario") or "").split("@")[0]

    def _verbo(r) -> str:
        return _ACAO_PART.get(str(r.get("acao") or "").lower(), str(r.get("acao") or ""))

    linhas: list[tuple] = []
    for r in lc.to_dict("records"):
        linhas.append((r.get("criado_em"),
                       f"comentário {_verbo(r)} em `{_obj(r)}` — {_quem(r)}"))
    for r in lt.to_dict("records"):
        linhas.append((r.get("criado_em"),
                       f"tag `{r.get('tag_chave', '')}` {_verbo(r)} em `{_obj(r)}` — {_quem(r)}"))
    df = pd.DataFrame(linhas, columns=["ts", "txt"])
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
        df = df.sort_values("ts", ascending=False, na_position="last")
    itens = [f"`{_fmt_ts(ts)}` · {txt}" for ts, txt in zip(df["ts"], df["txt"])][:limit] if not df.empty else []

    try:
        n_wk = _count(
            f"SELECT (SELECT count(*) FROM {_cad('log_comentarios')} "
            f"WHERE criado_em >= current_timestamp() - INTERVAL 7 DAYS) + "
            f"(SELECT count(*) FROM {_cad('log_tags')} "
            f"WHERE criado_em >= current_timestamp() - INTERVAL 7 DAYS)"
        )
    except Exception:
        n_wk = 0
    return itens, n_wk


def _atalho(chave: str, label: str, icon: str | None = None) -> None:
    """Renderiza um atalho (page_link) só se a página existe pro papel atual."""
    pg = st.session_state.get("_nav_pages", {}).get(chave)
    if pg is not None:
        st.page_link(pg, label=label, icon=icon)


def _nome_amigavel(email: str) -> str:
    """Nome de exibição do usuário logado: tenta o displayName do workspace/conta
    (SCIM), depois heurística sobre o e-mail. '' se não achar nada legível."""
    e = (email or "").strip()
    if not e:
        return ""
    try:
        for u in list_users_for_search():
            if str(u.get("email", "")).lower() == e.lower():
                nm = str(u.get("nome") or "").strip()
                if nm and nm.lower() != e.lower():
                    return nm.split()[0].capitalize() if " " in nm else nm
                break
    except Exception:
        pass
    raw = e.split("@")[0] if "@" in e else e
    if "." in raw:
        return raw.split(".")[0].capitalize()
    if 0 < len(raw) <= 14:
        return raw.capitalize()
    return ""


def page_inicio() -> None:
    st.markdown(_INICIO_CSS, unsafe_allow_html=True)
    user = st.session_state.get("user", "")
    role = st.session_state.get("role", "leitor")
    perms = st.session_state.get("perms", {}) or {}
    is_admin = role == "admin"
    can_aprov = is_admin or bool(perms.get("aprovador_tags"))
    can_cad = is_admin or bool(perms.get("ver_cadastros"))
    can_logs = is_admin or bool(perms.get("ver_logs"))
    is_power = bool(perms.get("power_steward"))

    try:
        doms, subs, stew = list_dominios(), list_subdominios(), list_stewards()
        glo, ind, dash = list_glossario_negocio(), list_indicadores(), list_dashboards()
    except Exception as exc:
        st.error(f"Não foi possível carregar o painel: {exc}")
        return

    # ---- Cabeçalho ----
    registrado = bool(perms.get("registrado", True))
    if not registrado:
        st.title("🧭 Olá, visitante")
    else:
        nome_cad = str(perms.get("nome") or "").strip()
        nome = (nome_cad.split()[0].capitalize() if nome_cad else "") or _nome_amigavel(user)
        st.title(f"🧭 Olá, {nome}" if nome else "🧭 Olá!")

    if not registrado:
        chips = ["visitante"]
    elif is_admin:
        chips = ["admin"] + (["Power Steward"] if is_power else [])
    else:
        chips = [role] + [
            lbl for flag, lbl in (
                ("power_steward", "Power Steward"),
                ("aprovador_tags", "Aprovador de tags"),
                ("ver_cadastros", "Ver cadastros"),
                ("ver_logs", "Ver logs"),
            ) if perms.get(flag)
        ]
    cls = "inicio-chip leitor" if role == "leitor" else "inicio-chip"
    st.markdown("".join(f'<span class="{cls}">{c}</span>' for c in chips), unsafe_allow_html=True)

    n_pend = len(list_tag_backlog("pendente")) if can_aprov else 0
    lacunas = _lacunas_cadastro(doms, subs, stew, glo, ind) if can_cad else []
    n_lac = sum(x[0] for x in lacunas)
    meus_ind = _meus_indicadores(ind, user) if (is_power or is_admin) else []

    if is_admin:
        partes = []
        if n_pend:
            partes.append(f"**{n_pend}** tag(s) aguardando aprovação")
        if n_lac:
            partes.append(f"**{n_lac}** lacuna(s) de cadastro")
        st.caption("Você tem " + (" e ".join(partes) + "." if partes else "tudo em dia por aqui. 🎉"))
    elif can_aprov:
        st.caption(f"Você tem **{n_pend}** tag(s) aguardando sua aprovação."
                   if n_pend else "Nenhuma tag aguardando aprovação. 🎉")
    elif is_power:
        st.caption(f"Você é Power Steward de **{len(meus_ind)}** indicador(es).")
    elif not registrado:
        st.caption("Você ainda não tem acesso cadastrado — pode consultar o glossário e o "
                   "Assistente. Peça a um admin para incluir seu e-mail em **Usuários & Permissões**.")
    else:
        st.caption("Acesso de leitura — use o glossário e o Assistente para explorar o que já existe.")

    st.divider()

    # ---- Números ----
    novos = _novos_na_semana()
    kpis = [
        ("Domínios", len(doms), novos.get("dominios")),
        ("Sub-domínios", len(subs), novos.get("subdominios")),
        ("Owners & Stewards", len(stew), novos.get("stewards")),
        ("Termos de negócio", len(glo), novos.get("termos")),
        ("Indicadores", len(ind), novos.get("indicadores")),
        ("Dashboards", len(dash), None),
    ]
    linha = st.columns(3) + st.columns(3)
    for col, (lbl, val, delta) in zip(linha, kpis):
        col.metric(lbl, val, delta=(f"+{delta} na semana" if delta else None))

    st.write("")
    esq, dir_ = st.columns(2)

    # ---- Pendências de aprovação ----
    if can_aprov:
        with esq.container(border=True, key="inicio_pend"):
            st.markdown(f"##### ⏳ Pendências de aprovação  ·  {n_pend}")
            if n_pend == 0:
                st.caption("Nada pendente. 🎉")
            else:
                pend = list_tag_backlog("pendente").head(5).to_dict("records")
                for r in pend:
                    obj = ".".join(str(x) for x in (r.get("catalogo"), r.get("db_schema"),
                                                    r.get("tabela"), r.get("coluna")) if x)
                    valor = r.get("valor_novo") or "—"
                    quem = str(r.get("solicitante") or "").split("@")[0]
                    st.markdown(
                        f"`{obj}` — tag `{r.get('tag_chave', '')}` → \"{valor}\"  \n"
                        f"<small>por {quem} · {_fmt_ts(r.get('criado_em'))}</small>",
                        unsafe_allow_html=True,
                    )
            _atalho("backlog", "Abrir backlog de aprovação", "✅")

    # ---- Saúde dos cadastros ----
    if can_cad:
        with dir_.container(border=True):
            st.markdown("##### 🩺 Saúde dos cadastros")
            if not lacunas:
                st.markdown("✅ Cadastros em dia.")
            else:
                for cnt, txt, chave in lacunas:
                    st.markdown(f"⚠️ &nbsp;{txt}", unsafe_allow_html=True)
                    if chave:
                        _atalho(chave, "corrigir")

    # ---- Meus indicadores ----
    if is_power or (is_admin and meus_ind):
        with esq.container(border=True):
            st.markdown("##### 📈 Meus indicadores")
            if not meus_ind:
                st.caption("Você ainda não é Power Steward de nenhum indicador.")
            else:
                dom_nome = {d["id"]: d["nome"] for d in doms.to_dict("records")}
                for r in meus_ind[:6]:
                    dm = dom_nome.get(r.get("dominio_id"), "—")
                    niv = r.get("nivel_apuracao") or "—"
                    st.markdown(f"**{r['nome']}** · {dm} · {niv}")
            _atalho("indicadores", "Abrir Indicadores", "📈")

    # ---- Atividade recente ----
    if can_logs:
        with dir_.container(border=True):
            itens, n_wk = _atividade_recente(8)
            st.markdown(f"##### 🕓 Atividade recente  ·  {n_wk} esta semana")
            if not itens:
                st.caption("Ainda não há alterações registradas.")
            else:
                for it in itens:
                    st.markdown(f"- {it}")
            _atalho("auditoria", "Ver relatório de auditoria", "📋")

    # ---- Leitor: comece por aqui ----
    if role == "leitor":
        with st.container(border=True):
            st.markdown("##### 👋 Comece por aqui")
            st.markdown(
                f"- **{len(glo)}** termos e **{len(ind)}** indicadores documentados no glossário."
            )
            _atalho("consulta", "Consultar o glossário", "📚")
            if LLM_ENABLED:
                st.caption("Dúvidas? Pergunte ao **Assistente de Governança** no painel à direita.")

    # ---- Atalhos ----
    with st.container(border=True):
        st.markdown("##### 🔗 Atalhos")
        a, b, c = st.columns(3)
        with a:
            _atalho("governanca", "Aplicar governança numa tabela", "🏷️")
            _atalho("consulta", "Consultar glossário", "📚")
        with b:
            if can_aprov:
                _atalho("backlog", "Aprovar tags pendentes", "✅")
            _atalho("indicadores", "Novo indicador", "📈")
        with c:
            _atalho("glossario_edit", "Novo termo de negócio", "📖")
            if can_logs:
                _atalho("auditoria", "Ver auditoria", "📋")


# ---------------------------------------------------------------------------
# Entrada / navegação
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="Governança & Cadastros — Unity Catalog",
        page_icon="🏷️",
        layout="wide",
    )

    if not WAREHOUSE_ID:
        st.error(
            "Variável de ambiente `DATABRICKS_WAREHOUSE_ID` não definida. "
            "Configure um SQL Warehouse no app.yaml. Veja o README.md."
        )
        st.stop()

    # Identidade do usuário (OBO) + papel/flags nos cadastros (RBAC).
    user = current_username()
    st.session_state["user"] = user
    perms = {
        "papel": "leitor", "ver_logs": False, "ver_cadastros": False,
        "aprovador_tags": False, "power_steward": False, "registrado": False, "nome": "",
    }
    try:
        ensure_cadastro_tables()
        perms = get_user_perms(user)
    except Exception as exc:
        st.session_state["cad_bootstrap_error"] = str(exc)
    role = perms["papel"]
    is_admin = role == "admin"
    st.session_state["role"] = role
    st.session_state["perms"] = perms

    render_sidebar()
    if st.session_state.get("cad_bootstrap_error"):
        st.sidebar.warning("Cadastros indisponíveis: " + st.session_state["cad_bootstrap_error"][:200])

    # Páginas — algumas guardadas em `nav_pages` pros atalhos da tela de Início
    # (só entram no dict se o papel permite, então os atalhos já respeitam o RBAC).
    nav_pages: dict = {}
    pg_inicio = st.Page(page_inicio, title="Início", icon="🧭", default=True)
    pg_governanca = st.Page(page_governanca, title="Governança de Dados — Unity Catalog", icon="🏷️")
    pg_consulta = st.Page(page_consulta_termos, title="Termos de Negócio", icon="📚")
    nav_pages["governanca"] = pg_governanca
    nav_pages["consulta"] = pg_consulta

    # Governança sempre visível. Cadastros/Auditoria conforme flags (admin vê tudo).
    governanca = [pg_governanca]
    try:
        for row in user_visible_dashboards(user, is_admin):
            governanca.append(
                st.Page(
                    make_dashboard_page(row), title=row["nome"], icon=row.get("icone") or "📊",
                    url_path=f"dashboard-{int(row['id'])}",
                )
            )
    except Exception as exc:
        st.session_state.setdefault("cad_bootstrap_error", str(exc))
    pages: dict = {"Painel": [pg_inicio]}
    if is_admin or perms["ver_cadastros"]:
        pg_stewards = st.Page(page_stewards, title="Data Owners & Stewards", icon="🧑‍💼")
        pg_glossario_edit = st.Page(page_glossario_negocio, title="Glossário de Negócio", icon="📖")
        pg_indicadores = st.Page(page_indicadores, title="Indicador", icon="📈")
        nav_pages.update(stewards=pg_stewards, glossario_edit=pg_glossario_edit, indicadores=pg_indicadores)
        cadastros = [
            st.Page(page_dominios, title="Domínios", icon="🗂️"),
            st.Page(page_subdominios, title="Sub-domínios", icon="🗃️"),
            pg_stewards,
            st.Page(page_dashboards, title="Dashboards", icon="📊"),
            st.Page(page_padroes_dado_pessoal, title="Padrões de Dado Pessoal", icon="🧬"),
            pg_glossario_edit,
            pg_indicadores,
        ]
        if is_admin:  # gestão de usuários/permissões é sempre admin-only
            cadastros.append(st.Page(page_permissoes, title="Usuários & Permissões", icon="🔒"))
        pages["Cadastros"] = cadastros
    pages["Governança"] = governanca
    pages["Glossário"] = [pg_consulta]
    if is_admin or perms["aprovador_tags"]:
        pg_backlog = st.Page(page_tag_backlog, title="Backlog de Aprovação de Tags", icon="✅")
        nav_pages["backlog"] = pg_backlog
        pages["Aprovações"] = [pg_backlog]
    if is_admin or perms["ver_logs"]:
        pg_relatorio = st.Page(page_relatorio_auditoria, title="Relatório de Auditoria", icon="📋")
        nav_pages["auditoria"] = pg_relatorio
        pages["Auditoria"] = [
            pg_relatorio,
            st.Page(page_log_comentarios, title="Log de comentários", icon="📜"),
            st.Page(page_log_tags, title="Log de tags", icon="🏷️"),
        ]
    st.session_state["_nav_pages"] = nav_pages
    nav = st.navigation(pages)

    nav.run()

    # Assistente de IA: painel ancorado à direita (segunda "sidebar"), que
    # recolhe para uma aba fina no canto direito. Streamlit só tem uma sidebar
    # nativa (esquerda), então o painel é um container fixo via CSS.
    if LLM_ENABLED:
        render_assistant_dock(user)


if __name__ == "__main__":
    main()
