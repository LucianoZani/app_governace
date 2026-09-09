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
- **Publicação de Metric View (indicador) = Service Principal** — outra exceção
  documentada: ``CREATE OR REPLACE VIEW … WITH METRICS`` roda com o SP (blueprint
  seção 5, Passo 5), porque cria um objeto novo no catálogo/schema alvo — exige
  ``CREATE VIEW`` no schema, grant que o usuário de negócio comum não tem. A
  fórmula (linguagem natural → SQL) passa por confirmação humana obrigatória
  (Passo 3) antes de qualquer publicação — nunca publica direto do que a IA gera.
- **Autenticação nativa do Databricks App**: o ``WorkspaceClient()`` usa
  automaticamente as credenciais injetadas no runtime do App.
- **Execução de SQL** via Statement Execution API do ``databricks-sdk``,
  usando um SQL Warehouse (id lido de variável de ambiente).

Veja o README.md para permissões e deploy.
"""

from __future__ import annotations

import base64
import calendar
import json
import os
import re
import time
import unicodedata
import uuid
from datetime import date, timedelta
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

# Nome de marca do app (título da página, sidebar, system prompt do
# assistente). Termo de negócio "Power Steward" (usuário marcado como
# responsável por um indicador) é outra coisa — não use esta var pra ele.
APP_NAME = os.environ.get("APP_NAME", "Power Steward").strip() or "Power Steward"

# Endpoint do LLM. Usado por (a) `gerar_expr_sql` (pipeline de publicação de
# indicador, blueprint seção 5.1, Passo 1) e (b) o painel "Assistente de
# Governança" (chat com IA, só metadado — ver docs-produto/15-seguranca-assistente.md).
#
# Dois formatos aceitos (ver `get_llm_client`), escolhidos pela presença de
# ponto no nome:
#   - "catalog.schema.model"  -> model service registrado em UC, via Unity AI
#     Gateway (/ai-gateway/mlflow/v1). O SP do app precisa de EXECUTE nele.
#   - "databricks-gpt-oss-120b" (nome simples) -> serving endpoint clássico
#     (/serving-endpoints), incl. as Foundation Model APIs pay-per-token. O SP
#     do app precisa de CAN QUERY no endpoint.
#
# ⚠️ `testar_candidato` e `obter_custo_por_dominio` rodam `prefer_user=True`
# (OBO): com `USE_ON_BEHALF_OF_USER=false` caem pro Service Principal. Ligar
# OBO de verdade esbarrou (2026-08-30) no token do usuário voltando sem o
# escopo `sql` mesmo com ele habilitado no App — pendente de investigar (ver
# `app.yaml`). O assistente de chat foi reintroduzido (2026-08-31) já com as
# tools de metadado em OBO + fail-closed + RBAC, e sem NENHUMA tool que lê
# linha de tabela — não é "consulta sobre dado real", é ajuda de metadado.
LLM_ENABLED = os.environ.get("LLM_ENABLED", "false").strip().lower() == "true"
LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "").strip()

# Quantidade de linhas de amostra exibidas por coluna.
SAMPLE_ROWS = 5

# Tempo máximo (segundos) aguardando a conclusão de um statement.
STATEMENT_TIMEOUT_S = 120

# Tabela de PROPOSTAS de descrição de coluna geradas por IA — habilita a
# worklist "Revisar catalogação feita com IA" no topo da página Governança de
# Dados. Formato: "catalog.schema.tabela". Vazio = a worklist não aparece.
#
# Colunas esperadas: catalogo, esquema, tabela, coluna, descricao_proposta,
# descricao_final, status ('pendente' = a revisar; 'aprovado'/'ajustado' =
# tratada; 'rejeitado'/'coluna_removida' = ignorar), modelo, proposto_em,
# revisado_por, revisado_em, aplicado_em.
#
# Quando um steward salva o comentário de uma coluna que tem proposta
# 'pendente', o app fecha a linha (status -> aprovado/ajustado, revisado_por,
# revisado_em, aplicado_em) — a worklist encolhe sozinha. Essa escrita é
# best-effort e roda OBO: o SP do app só precisa de MODIFY nessa tabela se o
# OBO cair pro service principal (USE_ON_BEHALF_OF_USER=false).
PROPOSTAS_IA_TABLE = os.environ.get("PROPOSTAS_IA_TABLE", "").strip()

# Snapshot de FinOps numa tabela Delta ("catalog.schema.tabela"). Alternativa
# ao OBO quando o Service Principal do app NÃO tem acesso a `system.billing`
# (o schema `system.billing` só concede ao grupo reservado `account admins`).
# Um job externo — rodando com uma identidade que TEM esse acesso — mantém a
# tabela fresca; o app a lê como SP. Colunas esperadas: dia, dominio,
# tipo_custo, dbus, custo_usd (mesmo shape de `obter_custo_por_dominio`).
# Ordem de fontes em `page_finops`: OBO ao vivo -> este snapshot -> xlsx demo.
# Vazio = comportamento antigo.
FINOPS_SNAPSHOT_TABLE = os.environ.get("FINOPS_SNAPSHOT_TABLE", "").strip()


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


def _debug_token_scope(token: str) -> dict:
    """DIAGNÓSTICO TEMPORÁRIO (remover depois de resolver o 403 de escopo do
    FinOps) — decodifica o payload do JWT em ``x-forwarded-access-token``
    (sem validar assinatura, só pra inspecionar claims) e devolve as claims
    relevantes. Nunca loga o token inteiro."""
    partes = token.split(".")
    if len(partes) != 3:
        return {"erro": "token não parece ser um JWT (não tem 3 partes separadas por '.')", "partes": len(partes)}
    payload_b64 = partes[1] + "=" * (-len(partes[1]) % 4)  # padding do base64url
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception as exc:
        return {"erro": f"falha ao decodificar payload: {exc}"}
    return {
        "scope": payload.get("scope") or payload.get("scp"),
        "aud": payload.get("aud"),
        "sub": payload.get("sub"),
        "client_id": payload.get("client_id") or payload.get("cid"),
        "exp": payload.get("exp"),
        "todas_as_claims": list(payload.keys()),
    }


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
    """Cliente OpenAI-compatible para o LLM do workspace.

    Aponta para o Unity AI Gateway (``/ai-gateway/mlflow/v1``) quando
    ``LLM_ENDPOINT`` é um full name de UC (``catalog.schema.model``), ou para
    os serving endpoints clássicos (``/serving-endpoints``) quando é um nome
    simples (ex.: ``databricks-gpt-oss-120b`` — Foundation Model APIs).

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
    host = cfg.host.rstrip("/")
    suffix = "/ai-gateway/mlflow/v1" if "." in LLM_ENDPOINT else "/serving-endpoints"
    return OpenAI(api_key=token, base_url=f"{host}{suffix}")


# ---------------------------------------------------------------------------
# Helpers de SQL (quoting seguro + execução)
# ---------------------------------------------------------------------------


def q_ident(name: str) -> str:
    """Quota um identificador com crase, escapando crases internas."""
    return "`" + name.replace("`", "``") + "`"


def q_full(catalog: str, schema: str, table: str) -> str:
    """Nome totalmente qualificado e quotado da tabela."""
    return f"{q_ident(catalog)}.{q_ident(schema)}.{q_ident(table)}"


def q_fqn(dotted: str) -> str:
    """Quota um nome pontuado ("catalog.schema.tabela") parte por parte."""
    return ".".join(q_ident(p.strip()) for p in dotted.split(".") if p.strip())


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


@st.cache_data(ttl=300, show_spinner=False)
def list_tables_with_comment(user: str, catalog: str, schema: str) -> list[dict]:
    """Tabelas de um schema + comentário (metadados, OBO). Para o assistente."""
    sql = (
        f"SELECT table_name, comment FROM {q_ident(catalog)}.information_schema.tables "
        f"WHERE table_schema = {q_str(schema)} ORDER BY table_name"
    )
    df = run_query(sql, prefer_user=True)
    if df.empty:
        return []
    return [
        {"tabela": r["table_name"], "comentario": r.get("comment") or ""}
        for _, r in df.iterrows()
    ]


@st.cache_data(ttl=120, show_spinner=False)
def search_columns(user: str, termo: str, catalog: str | None = None, limit: int = 60) -> list[dict]:
    """Busca colunas cujo NOME ou COMENTÁRIO contém ``termo`` (case-insensitive),
    varrendo os catálogos permitidos via information_schema. Só metadados
    (nome/tipo/comentário/tabela) — nunca valores. Roda sob OBO."""
    termo = (termo or "").strip().lower()
    if not termo:
        return []
    if catalog:
        cats = [catalog] if (not ALLOWED_CATALOGS or catalog.lower() in ALLOWED_CATALOGS) else []
    else:
        cats = sorted(ALLOWED_CATALOGS) if ALLOWED_CATALOGS else []
    out: list[dict] = []
    for cat in cats:
        sql = f"""
            SELECT table_schema, table_name, column_name, full_data_type, comment
            FROM {q_ident(cat)}.information_schema.columns
            WHERE contains(lower(column_name), {q_str(termo)})
               OR contains(lower(coalesce(comment, '')), {q_str(termo)})
            ORDER BY table_schema, table_name, ordinal_position
            LIMIT {int(limit)}
        """
        try:
            df = run_query(sql, prefer_user=True)
        except Exception:
            continue
        for _, r in df.iterrows():
            out.append({
                "catalogo": cat,
                "schema": r["table_schema"],
                "tabela": r["table_name"],
                "coluna": r["column_name"],
                "tipo": r["full_data_type"],
                "comentario": r.get("comment") or "",
            })
        if len(out) >= limit:
            break
    return out[:limit]


@st.cache_data(ttl=120, show_spinner=False)
def search_by_tag(
    user: str,
    catalog: str | None = None,
    schema: str | None = None,
    tag_key: str | None = None,
    tag_value: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Varre as tags governadas APLICADAS (não o catálogo de policies) num
    catálogo/schema inteiro, via ``information_schema.column_tags`` +
    ``.table_tags``. Uma query por catálogo — responde "quais tabelas/colunas
    têm a tag X / o valor Y" sem inspecionar tabela por tabela.

    Só metadados (schema/tabela/coluna/tag/valor) — nunca dado. Roda sob OBO.
    """
    if catalog:
        cats = [catalog] if (not ALLOWED_CATALOGS or catalog.lower() in ALLOWED_CATALOGS) else []
    else:
        cats = sorted(ALLOWED_CATALOGS) if ALLOWED_CATALOGS else []

    def _conds(schema_col: str) -> str:
        c = []
        if schema:
            c.append(f"{schema_col} = {q_str(schema)}")
        if tag_key:
            c.append(f"lower(tag_name) = {q_str(tag_key.strip().lower())}")
        if tag_value:
            c.append(f"contains(lower(coalesce(tag_value, '')), {q_str(tag_value.strip().lower())})")
        return (" WHERE " + " AND ".join(c)) if c else ""

    out: list[dict] = []
    for cat in cats:
        col_sql = f"""
            SELECT schema_name, table_name, column_name, tag_name, tag_value
            FROM {q_ident(cat)}.information_schema.column_tags{_conds('schema_name')}
            ORDER BY schema_name, table_name, column_name
            LIMIT {int(limit)}
        """
        tbl_sql = f"""
            SELECT schema_name, table_name, tag_name, tag_value
            FROM {q_ident(cat)}.information_schema.table_tags{_conds('schema_name')}
            ORDER BY schema_name, table_name
            LIMIT {int(limit)}
        """
        try:
            cdf = run_query(col_sql, prefer_user=True)
            for _, r in cdf.iterrows():
                out.append({
                    "catalogo": cat, "schema": r["schema_name"], "tabela": r["table_name"],
                    "coluna": r["column_name"], "tag": r["tag_name"], "valor": r.get("tag_value") or "",
                })
        except Exception:
            pass
        try:
            tdf = run_query(tbl_sql, prefer_user=True)
            for _, r in tdf.iterrows():
                out.append({
                    "catalogo": cat, "schema": r["schema_name"], "tabela": r["table_name"],
                    "coluna": None, "tag": r["tag_name"], "valor": r.get("tag_value") or "",
                })
        except Exception:
            pass
        if len(out) >= limit:
            break
    return out[:limit]



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
        st.markdown(f"## 🏷️ {APP_NAME}")
        st.caption("Governança de dados no Unity Catalog")
        st.divider()
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
    """Seletores encadeados de Catalog → Schema → Table (visíveis ao usuário).

    Aceita um "preset" one-shot vindo da worklist "Revisar catalogação feita com
    IA": ``st.session_state["_gov_preset"] = (catalog, schema, table)``. Ele é
    consumido (``pop``) e gravado no state das keys dos selectboxes; depois disso
    o usuário navega livremente e a seleção persiste entre reruns (ex.: após
    salvar um comentário) pelo state das próprias keys.
    """
    c1, c2, c3 = st.columns(3)

    preset = st.session_state.pop("_gov_preset", None)

    with c1:
        catalogs = list_catalogs(user)
        if preset and preset[0] in catalogs:
            st.session_state["gov_sel_cat"] = preset[0]
        if st.session_state.get("gov_sel_cat") not in catalogs:
            st.session_state.pop("gov_sel_cat", None)  # limpa state órfão
        catalog = st.selectbox(
            "Catalog", options=catalogs, index=None,
            placeholder="Selecione…", key="gov_sel_cat",
        )

    schema = None
    with c2:
        if catalog:
            # Filtra pelos schemas do ambiente lógico deste app (dev/prd).
            schemas = [s for s in list_schemas(user, catalog) if schema_belongs_to_env(s)]
            if preset and preset[0] == catalog and preset[1] in schemas:
                st.session_state["gov_sel_sch"] = preset[1]
            if st.session_state.get("gov_sel_sch") not in schemas:
                st.session_state.pop("gov_sel_sch", None)
            schema = st.selectbox(
                "Schema",
                options=schemas,
                index=None,
                placeholder="Selecione…",
                help=f"Exibindo apenas schemas de **{ENVIRONMENT.upper()}**.",
                key="gov_sel_sch",
            )

    table = None
    with c3:
        if catalog and schema:
            tables = list_tables(user, catalog, schema)
            if preset and preset[0] == catalog and preset[1] == schema and preset[2] in tables:
                st.session_state["gov_sel_tbl"] = preset[2]
            if st.session_state.get("gov_sel_tbl") not in tables:
                st.session_state.pop("gov_sel_tbl", None)
            table = st.selectbox(
                "Table", options=tables, index=None,
                placeholder="Selecione…", key="gov_sel_tbl",
            )

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
            # via_obo=False identifica unicamente o statement de comentário:
            # fecha a linha de proposta de IA correspondente (se houver).
            if not via_obo:
                _marcar_proposta_ia_revisada(
                    user, catalog, schema, table, column, new_comment
                )
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


# ---------------------------------------------------------------------------
# Worklist "Revisar catalogação feita com IA" (PROPOSTAS_IA_TABLE)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=120, show_spinner=False)
def list_tabelas_com_proposta_ia(user: str) -> pd.DataFrame:
    """Tabelas com descrições sugeridas por IA ainda **pendentes** de revisão.

    Lê ``PROPOSTAS_IA_TABLE`` via OBO (o parâmetro ``user`` chaveia o cache).
    Uma linha por (catálogo, schema, tabela). Vazio se a env var não estiver
    definida ou não houver pendências. NÃO filtra por ``ALLOWED_CATALOGS`` aqui
    — a página separa o que está dentro/fora do allowlist.
    """
    if not PROPOSTAS_IA_TABLE:
        return pd.DataFrame()
    sql = (
        "SELECT catalogo, esquema, tabela, "
        "count(*) AS colunas_pendentes, "
        "max(modelo) AS modelo, max(proposto_em) AS proposto_em "
        f"FROM {q_fqn(PROPOSTAS_IA_TABLE)} "
        "WHERE lower(status) = 'pendente' "
        "GROUP BY catalogo, esquema, tabela "
        "ORDER BY proposto_em DESC"
    )
    return run_query(sql, prefer_user=True)


def _marcar_proposta_ia_revisada(
    user: str, catalog: str, schema: str, table: str, column: str, texto_final: str,
) -> None:
    """Fecha a linha de proposta de IA quando o steward salva o comentário.

    Best-effort: falha aqui NUNCA bloqueia a governança (o comentário já foi
    aplicado no Unity Catalog neste ponto). ``status`` vira ``aprovado`` se o
    texto salvo é igual à sugestão da IA, ou ``ajustado`` se foi editado.
    """
    if not PROPOSTAS_IA_TABLE:
        return
    try:
        run_exec(
            f"MERGE INTO {q_fqn(PROPOSTAS_IA_TABLE)} AS d USING (SELECT "
            f"{q_str(catalog)} AS c, {q_str(schema)} AS e, {q_str(table)} AS t, "
            f"{q_str(column)} AS col, {q_str(texto_final)} AS txt) AS s "
            "ON lower(d.catalogo) = lower(s.c) AND lower(d.esquema) = lower(s.e) "
            "AND lower(d.tabela) = lower(s.t) AND lower(d.coluna) = lower(s.col) "
            "WHEN MATCHED AND lower(d.status) = 'pendente' THEN UPDATE SET "
            "d.descricao_final = s.txt, "
            "d.status = CASE WHEN trim(coalesce(d.descricao_proposta, '')) = trim(s.txt) "
            "THEN 'aprovado' ELSE 'ajustado' END, "
            f"d.revisado_por = {q_str(user)}, d.revisado_em = current_timestamp(), "
            "d.aplicado_em = current_timestamp()",
            prefer_user=True,
        )
        try:
            list_tabelas_com_proposta_ia.clear()
        except Exception:
            pass
    except Exception:
        pass


def _render_worklist_ia(user: str) -> None:
    """Bloco no topo da página: liga/desliga a worklist de catalogação por IA.

    Ao escolher uma tabela e clicar em "Abrir", grava o preset em
    ``st.session_state["_gov_preset"]`` e faz rerun — o ``select_object`` abaixo
    consome esse preset e já abre a tabela.
    """
    if not PROPOSTAS_IA_TABLE:
        return

    ligado = st.toggle(
        "🤖 Revisar catalogação feita com IA",
        key="_ia_modo",
        help=(
            "Lista as tabelas com descrições de coluna sugeridas por IA que "
            "ainda não foram revisadas. Escolha uma para revisar/ajustar os "
            "comentários — ao salvar, a linha sai da lista."
        ),
    )
    if not ligado:
        return

    try:
        df = list_tabelas_com_proposta_ia(user)
    except Exception as exc:
        st.warning(
            f"Não foi possível ler as propostas de IA em `{PROPOSTAS_IA_TABLE}`: {exc}"
        )
        return

    if df.empty:
        st.success("Nenhuma tabela com catalogação de IA pendente. 🎉")
        return

    if ALLOWED_CATALOGS:
        _cat = df["catalogo"].astype(str).str.lower()
        dentro = df[_cat.isin(ALLOWED_CATALOGS)].reset_index(drop=True)
        fora = len(df) - len(dentro)
    else:
        dentro, fora = df, 0

    if fora:
        st.caption(
            f"{fora} tabela(s) com pendências em catálogos fora do allowlist "
            "deste app — não listadas."
        )
    if len(dentro) == 0:
        st.info(
            "As pendências de catalogação por IA estão todas em catálogos fora "
            "do allowlist deste app."
        )
        return

    st.dataframe(
        dentro.rename(columns={
            "catalogo": "Catálogo", "esquema": "Schema", "tabela": "Tabela",
            "colunas_pendentes": "Colunas a revisar", "modelo": "Modelo IA",
            "proposto_em": "Gerado em",
        }),
        use_container_width=True, hide_index=True,
    )

    recs = dentro.to_dict("records")
    opts = [
        f'{r["catalogo"]}.{r["esquema"]}.{r["tabela"]}  '
        f'({int(r["colunas_pendentes"])} coluna(s))'
        for r in recs
    ]
    c_sel, c_btn = st.columns([4, 1])
    with c_sel:
        sel = st.selectbox(
            "Abrir tabela para revisar", options=["—"] + opts,
            key="_ia_sel", label_visibility="collapsed",
        )
    with c_btn:
        if st.button("Abrir ▸", use_container_width=True, disabled=(sel == "—")):
            r = recs[opts.index(sel)]
            st.session_state["_gov_preset"] = (r["catalogo"], r["esquema"], r["tabela"])
            st.rerun()

    st.divider()


@st.cache_data(ttl=60, show_spinner=False)
def _propostas_pendentes_da_tabela(
    user: str, catalog: str, schema: str, table: str
) -> pd.DataFrame:
    """Colunas da tabela aberta que têm descrição de IA com status='pendente'."""
    if not PROPOSTAS_IA_TABLE:
        return pd.DataFrame()
    sql = (
        "SELECT coluna, tipo_dado, coalesce(descricao_proposta, '') AS descricao_proposta "
        f"FROM {q_fqn(PROPOSTAS_IA_TABLE)} "
        "WHERE lower(status) = 'pendente' "
        f"AND lower(catalogo) = lower({q_str(catalog)}) "
        f"AND lower(esquema) = lower({q_str(schema)}) "
        f"AND lower(tabela) = lower({q_str(table)}) "
        "ORDER BY coluna"
    )
    return run_query(sql, prefer_user=True)


def _aplicar_revisao_ia(
    user: str, catalog: str, schema: str, table: str,
    itens: list[tuple[str, str]],
) -> None:
    """Aplica as descrições revisadas como COMMENT ON COLUMN (via SP) e fecha as
    linhas da tabela de propostas. Best-effort por coluna: uma falha não derruba
    as outras."""
    if not user_can_access_table(user, catalog, schema, table):
        st.session_state["save_feedback"] = [(
            "error",
            "Você não tem acesso a esta tabela — nenhuma descrição foi aplicada.",
        )]
        st.rerun()
        return

    full = q_full(catalog, schema, table)
    atual = {c.name: (c.comment or "") for c in get_columns(user, catalog, schema, table)}

    ok, falhas = 0, []
    for col, txt in itens:
        txt = (txt or "").strip()
        if not txt:
            continue
        try:
            run_exec(f"COMMENT ON COLUMN {full}.{q_ident(col)} IS {q_str(txt)}")  # SP
            _log_comment_change(
                user, "coluna", catalog, schema, table, col, atual.get(col, ""), txt
            )
            _marcar_proposta_ia_revisada(user, catalog, schema, table, col, txt)
            ok += 1
        except Exception as exc:
            falhas.append(f"{col} — {exc}")

    fb: list[tuple[str, str]] = []
    if ok:
        fb.append(("success", f"✅ {ok} descrição(ões) de IA revisada(s) e aplicada(s)."))
    for f in falhas:
        fb.append(("error", f"❌ {f}"))
    if not fb:
        fb.append(("warning", "Nada a aplicar."))
    st.session_state["save_feedback"] = fb
    if ok:
        get_columns.clear()
        _propostas_pendentes_da_tabela.clear()
        try:
            list_tabelas_com_proposta_ia.clear()
        except Exception:
            pass
    st.rerun()


def _render_revisao_ia_tabela(
    user: str, catalog: str, schema: str, table: str, columns: list["ColumnMeta"],
) -> None:
    """Painel de revisão das descrições sugeridas por IA para a tabela aberta.

    Aparece sempre que a tabela tem coluna com proposta `pendente` — não depende
    do toggle da worklist. A ação principal é "aplicar tudo" (o texto da IA vira
    o comentário); o expander permite ajustar antes.
    """
    if not PROPOSTAS_IA_TABLE:
        return
    try:
        props = _propostas_pendentes_da_tabela(user, catalog, schema, table)
    except Exception as exc:
        st.warning(
            f"Não foi possível ler as propostas de IA em `{PROPOSTAS_IA_TABLE}`: {exc}"
        )
        return
    if props.empty:
        return

    comentario_atual = {c.name: (c.comment or "") for c in columns}
    props = props.copy()
    props["comentario_atual"] = props["coluna"].map(lambda c: comentario_atual.get(c, ""))

    st.divider()
    st.markdown("### 🤖 Catalogação sugerida por IA")
    st.caption(
        f"**{len(props)} coluna(s)** desta tabela têm descrição sugerida por IA "
        "ainda **não revisada**. Confira e aplique — o texto vira o comentário "
        "da coluna e a pendência é fechada (marcada como revisada). Não precisa "
        "editar; ajuste só se alguma descrição estiver errada."
    )

    vis = props[["coluna", "tipo_dado", "comentario_atual", "descricao_proposta"]].rename(
        columns={
            "coluna": "Coluna", "tipo_dado": "Tipo",
            "comentario_atual": "Comentário atual", "descricao_proposta": "Descrição sugerida",
        }
    )
    st.dataframe(vis, use_container_width=True, hide_index=True)

    key_base = f"{catalog}.{schema}.{table}"
    if st.button(
        f"✅ Revisado — aplicar as {len(props)} descrições",
        type="primary", key=f"ia_rev_ok_{key_base}",
    ):
        _aplicar_revisao_ia(
            user, catalog, schema, table,
            [(r["coluna"], r["descricao_proposta"]) for _, r in props.iterrows()],
        )

    with st.expander("✏️ Ajustar alguma descrição antes de aplicar"):
        edit = props[["coluna", "descricao_proposta"]].rename(
            columns={"coluna": "Coluna", "descricao_proposta": "Descrição"}
        )
        edited = st.data_editor(
            edit, hide_index=True, use_container_width=True,
            disabled=["Coluna"], key=f"ia_rev_ed_{key_base}",
        )
        if st.button("Aplicar com meus ajustes", key=f"ia_rev_ed_ok_{key_base}"):
            _aplicar_revisao_ia(
                user, catalog, schema, table,
                [(r["Coluna"], r["Descrição"]) for _, r in edited.iterrows()],
            )


def page_governanca() -> None:
    """Página: Governança de Dados (tags governadas + comentários no Unity Catalog)."""
    st.title(f"🏷️ {APP_NAME} — Governança de Dados")
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

    # Worklist opcional de catalogação por IA — antes da escolha de objeto.
    _render_worklist_ia(user)

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

    # Descrições sugeridas por IA para esta tabela ainda não revisadas
    # (aparece só se houver pendências — não depende do toggle da worklist).
    _render_revisao_ia_tabela(user, catalog, schema, table, columns)

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
# Schema dos cadastros internos do app. Dois modelos, escolhidos por
# CADASTRO_SCHEMA_ENV_SUFFIX:
#   "true"  (padrão) — CADASTRO_SCHEMA é a BASE e o app acrescenta o sufixo do
#           ENVIRONMENT (…_dev / …_prd). Um metastore único com catálogo `apps`
#           compartilhado entre DEV e PROD, separação por sufixo de schema.
#   "false" — CADASTRO_SCHEMA é o nome COMPLETO do schema, sem sufixo. Para
#           instalações com um schema pré-provisionado de nome fixo, ou com
#           catálogos separados por ambiente (ex.: comgas_dev / comgas_prd).
_CAD_SCHEMA_BASE = os.environ.get("CADASTRO_SCHEMA", "governanca_unity_catalog").strip()
_CAD_SCHEMA_ENV_SUFFIX = os.environ.get("CADASTRO_SCHEMA_ENV_SUFFIX", "true").strip().lower() == "true"
CAD_SCHEMA = f"{_CAD_SCHEMA_BASE}_{ENVIRONMENT}" if _CAD_SCHEMA_ENV_SUFFIX else _CAD_SCHEMA_BASE
SEED_ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "t.guilherme.massafer@ero.com").strip().lower()


def _cad(table: str) -> str:
    """Nome totalmente qualificado de uma tabela de cadastro.

    Glossário de negócio e indicadores (`glossario_negocio`, `indicadores`)
    também vivem aqui, junto com as demais tabelas de cadastro — schema único
    `CAD_SCHEMA`, sem schema `ontologia_<env>` separado (consolidado em
    2026-08-30; ver migração dos dados de `ontologia_<env>` para cá).
    """
    return f"{q_ident(CAD_CATALOG)}.{q_ident(CAD_SCHEMA)}.{q_ident(table)}"


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
        # Hierarquia de negócio de 3 níveis: Franquia › Domínio › Sub-domínio.
        # `franquias` é o nível de topo (PoC — cliente decide se vira entidade
        # definitiva); `dominios.franquia_id` liga cada domínio a uma franquia
        # (nullable p/ compat com base pré-existente — ver ALTER TABLE abaixo).
        f"CREATE TABLE IF NOT EXISTS {_cad('franquias')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, nome STRING, descricao STRING, {audit})",
        f"CREATE TABLE IF NOT EXISTS {_cad('dominios')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, franquia_id BIGINT, nome STRING, descricao STRING, {audit})",
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
        f"CREATE TABLE IF NOT EXISTS {_cad('glossario_negocio')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, "
        f"tipo STRING, nome STRING, objetivo STRING, observacoes STRING, "
        f"palavras_chave STRING, macroprocesso STRING, "
        f"dominio_id BIGINT, subdominio_id BIGINT, data_owner STRING, data_steward STRING, "
        f"rotulo_seguranca STRING, rotulo_privacidade STRING, {audit})",
        f"CREATE TABLE IF NOT EXISTS {_cad('indicadores')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, "
        f"tipo STRING, nome STRING, objetivo STRING, observacoes STRING, "
        f"palavras_chave STRING, macroprocesso STRING, "
        f"dominio_id BIGINT, subdominio_id BIGINT, "
        f"power_steward STRING, data_owner STRING, data_steward STRING, "
        f"rotulo_seguranca STRING, rotulo_privacidade STRING, "
        f"nivel_apuracao STRING, unidade STRING, variaveis_utilizadas STRING, "
        f"memoria_calculo STRING, restricoes STRING, "
        f"dimensao_tabelas STRING, metrica_tabelas STRING, "
        f"status_publicacao STRING, expr_validada STRING, metric_view_publicada STRING, {audit})",
        # --- Cadastro de Acesso a Dados (blueprint "Cadastro de Acesso") ---
        # Tabelas de apoio que alimentam políticas ABAC (row filter / column
        # mask) do Unity Catalog. Hoje mantidas via SQL na mão; aqui viram CRUD.
        # SKELETON: por ora vivem no schema de cadastros do app; na instalação
        # real (Comgás) devem ir para um schema de SEGURANÇA dedicado, com read
        # para o contexto que executa o UDF ABAC — ver blueprint. Duas tabelas
        # INDEPENDENTES por design (uma não deriva da outra).
        #
        # `usuario` = identificador que o UDF ABAC casa com current_user()
        # (e-mail p/ pessoa, application-id p/ SP). `dominio_id`/`subdominio_id`
        # referenciam o cadastro vivo de domínio; `dominio` guarda o nome
        # denormalizado (o que a tag `domain` dos dados costuma usar) — Comgás
        # confirma o formato (slug vs. nome).
        f"CREATE TABLE IF NOT EXISTS {_cad('mapa_dominio_acesso')} "
        f"(id STRING, grupo STRING, grupo_id STRING, usuario STRING, "
        f"dominio_id BIGINT, subdominio_id BIGINT, dominio STRING, subdominio STRING, {audit})",
        f"CREATE TABLE IF NOT EXISTS {_cad('mapa_sensibilidade_acesso')} "
        f"(id STRING, grupo STRING, grupo_id STRING, usuario STRING, "
        f"nivel_max_confidencialidade STRING, pode_ver_dado_pessoal BOOLEAN, "
        f"pode_ver_dado_pessoal_sensivel BOOLEAN, {audit})",
        # Log genérico de CRUD de cadastro (antes/depois) — o app só tinha
        # auditoria em linha + os logs específicos de comentário/tag. Reusável
        # por qualquer cadastro; por ora só as duas telas de Acesso a Dados.
        f"CREATE TABLE IF NOT EXISTS {_cad('log_cadastros')} "
        f"(id BIGINT GENERATED ALWAYS AS IDENTITY, usuario STRING, tabela STRING, "
        f"operacao STRING, registro_id STRING, antes STRING, depois STRING, criado_em TIMESTAMP)",
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
        for col in ("ver_logs", "ver_cadastros", "aprovador_tags", "power_steward",
                    "ver_finops", "engenharia", "admin_acesso"):
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
    # Coluna `franquia_id` em `dominios` (idempotente) — nível "Franquia"
    # acrescentado acima do domínio. Nullable: base pré-existente fica sem
    # franquia até alguém editar cada domínio na tela.
    try:
        cols_df = run_query(
            f"SELECT lower(column_name) AS c FROM {q_ident(CAD_CATALOG)}.information_schema.columns "
            f"WHERE lower(table_schema) = {q_str(CAD_SCHEMA.lower())} "
            f"AND lower(table_name) = 'dominios'"
        )
        existing_cols = set(cols_df["c"].tolist()) if not cols_df.empty else set()
        if "franquia_id" not in existing_cols:
            run_exec(f"ALTER TABLE {_cad('dominios')} ADD COLUMNS (franquia_id BIGINT)")
    except Exception:
        pass
    # Coluna `power_steward` em `indicadores` (idempotente p/ a tabela já
    # existente). Guarda o e-mail do Power Steward escolhido — a lista vem de
    # `permissoes` (flag `power_steward`).
    try:
        cols_df = run_query(
            f"SELECT lower(column_name) AS c FROM {q_ident(CAD_CATALOG)}.information_schema.columns "
            f"WHERE lower(table_schema) = {q_str(CAD_SCHEMA.lower())} "
            f"AND lower(table_name) = 'indicadores'"
        )
        existing_cols = set(cols_df["c"].tolist()) if not cols_df.empty else set()
        if "power_steward" not in existing_cols:
            run_exec(f"ALTER TABLE {_cad('indicadores')} ADD COLUMNS (power_steward STRING)")
        # Coluna `status_publicacao` (blueprint seção 5.1, Passo 0) — gate do
        # pipeline de publicação: rascunho -> pronto_para_ia -> validado ->
        # publicado. Elegível para pronto_para_ia só quando já tem lineage
        # real (dimensao_tabelas e metrica_tabelas não vazios); do contrário
        # fica preso em rascunho. Linhas existentes são backfilladas com a
        # mesma regra usada em cada save daqui pra frente (ver
        # _render_glossario_editor).
        if "status_publicacao" not in existing_cols:
            run_exec(f"ALTER TABLE {_cad('indicadores')} ADD COLUMNS (status_publicacao STRING)")
            run_exec(
                f"UPDATE {_cad('indicadores')} SET status_publicacao = "
                f"CASE WHEN coalesce(dimensao_tabelas, '[]') <> '[]' "
                f"AND coalesce(metrica_tabelas, '[]') <> '[]' "
                f"THEN 'pronto_para_ia' ELSE 'rascunho' END "
                f"WHERE status_publicacao IS NULL"
            )
        # Colunas `expr_validada` (Passo 3 — expressão SQL confirmada pelo
        # humano, o que de fato vira `measures[].expr` no Metric View) e
        # `metric_view_publicada` (Passo 5 — nome totalmente qualificado da
        # view criada, pra rastreabilidade). Sem backfill: só existem depois
        # que o indicador passa pelo fluxo novo.
        if "expr_validada" not in existing_cols:
            run_exec(f"ALTER TABLE {_cad('indicadores')} ADD COLUMNS (expr_validada STRING)")
        if "metric_view_publicada" not in existing_cols:
            run_exec(f"ALTER TABLE {_cad('indicadores')} ADD COLUMNS (metric_view_publicada STRING)")
        # Colunas de negócio adicionais: `dimensoes_negocio` (descrição em
        # linguagem de negócio dos recortes desejados — orienta a Engenharia
        # na escolha das colunas de Dimensão, mas não é lineage técnico) e
        # `decisao_negocio` (que decisão esse indicador apoia). Esta última é
        # concatenada com `objetivo` no comentário da Metric View publicada
        # — ver `montar_yaml_metric_view`.
        if "dimensoes_negocio" not in existing_cols:
            run_exec(f"ALTER TABLE {_cad('indicadores')} ADD COLUMNS (dimensoes_negocio STRING)")
        if "decisao_negocio" not in existing_cols:
            run_exec(f"ALTER TABLE {_cad('indicadores')} ADD COLUMNS (decisao_negocio STRING)")
    except Exception:
        pass
    # Migração da antiga `termos_negocio` (registro único com seletor de tipo)
    # para as duas tabelas novas. Roda uma vez: copia por tipo e dropa a origem.
    # Idempotente — se `termos_negocio` não existe mais, não faz nada. Se algum
    # INSERT falhar, o DROP não roda e o próximo boot tenta de novo.
    try:
        tbl_df = run_query(
            f"SELECT lower(table_name) AS t FROM {q_ident(CAD_CATALOG)}.information_schema.tables "
            f"WHERE lower(table_schema) = {q_str(CAD_SCHEMA.lower())} "
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
            if _count(f"SELECT count(*) FROM {_cad('glossario_negocio')}") == 0:
                run_exec(
                    f"INSERT INTO {_cad('glossario_negocio')} (tipo, {_comuns}) "
                    f"SELECT 'Termo', {_comuns} FROM {_cad('termos_negocio')} "
                    f"WHERE lower(coalesce(tipo, 'termo')) <> 'indicador'"
                )
            if _count(f"SELECT count(*) FROM {_cad('indicadores')}") == 0:
                run_exec(
                    f"INSERT INTO {_cad('indicadores')} "
                    f"(tipo, {_comuns}, {_ind}, dimensao_tabelas, metrica_tabelas) "
                    f"SELECT 'Indicador', {_comuns}, {_ind}, "
                    f"coalesce(dimensao_tabelas, '[]'), coalesce(metrica_tabelas, '[]') "
                    f"FROM {_cad('termos_negocio')} WHERE lower(tipo) = 'indicador'"
                )
            # Só dropa a origem depois que os dois INSERTs acima passaram sem
            # exceção (o try/except garante isso).
            run_exec(f"DROP TABLE IF EXISTS {_cad('termos_negocio')}")
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
    "ver_finops", "power_steward", "engenharia"}``. Para não-admin, as flags
    vêm das colunas homônimas de ``permissoes`` (default False).
    """
    base = {
        "papel": "leitor", "ver_logs": False, "ver_cadastros": False,
        "aprovador_tags": False, "ver_finops": False, "power_steward": False,
        "engenharia": False, "admin_acesso": False, "registrado": False, "nome": "",
    }
    if not email:
        return base
    df = run_query(
        f"SELECT coalesce(nome, '') AS nome, papel, ver_logs, ver_cadastros, "
        f"aprovador_tags, ver_finops, power_steward, engenharia, admin_acesso "
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
            "aprovador_tags": True, "ver_finops": True, "power_steward": power_steward,
            "engenharia": True, "admin_acesso": True, "registrado": True, "nome": nome,
        }
    return {
        "papel": papel,
        "ver_logs": _as_bool(row["ver_logs"]),
        "ver_cadastros": _as_bool(row["ver_cadastros"]),
        "aprovador_tags": _as_bool(row["aprovador_tags"]),
        "ver_finops": _as_bool(row["ver_finops"]),
        "power_steward": power_steward,
        "engenharia": _as_bool(row["engenharia"]),
        "admin_acesso": _as_bool(row["admin_acesso"]),
        "registrado": True,
        "nome": nome,
    }


def can_edit(role: str) -> bool:
    return role in ("admin", "editor")


# ---- Leituras (SP; cache curto + refresh manual na sidebar) ----
@st.cache_data(ttl=30, show_spinner=False)
def list_franquias() -> pd.DataFrame:
    return run_query(f"SELECT id, nome, descricao FROM {_cad('franquias')} ORDER BY nome")


@st.cache_data(ttl=30, show_spinner=False)
def list_dominios() -> pd.DataFrame:
    return run_query(
        f"SELECT id, franquia_id, nome, descricao FROM {_cad('dominios')} ORDER BY nome"
    )


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
        f"SELECT {_GLOSSARIO_COLS_COMUNS} FROM {_cad('glossario_negocio')} ORDER BY nome"
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_indicadores() -> pd.DataFrame:
    return run_query(
        f"SELECT {_GLOSSARIO_COLS_COMUNS}, power_steward, nivel_apuracao, unidade, "
        f"variaveis_utilizadas, memoria_calculo, restricoes, "
        f"dimensoes_negocio, decisao_negocio, "
        f"dimensao_tabelas, metrica_tabelas, status_publicacao, "
        f"expr_validada, metric_view_publicada "
        f"FROM {_cad('indicadores')} ORDER BY nome"
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
        f"CAST(NULL AS STRING) AS dimensoes_negocio, CAST(NULL AS STRING) AS decisao_negocio, "
        f"'[]' AS dimensao_tabelas, '[]' AS metrica_tabelas "
        f"FROM {_cad('glossario_negocio')} "
        f"UNION ALL "
        f"SELECT {_GLOSSARIO_COLS_COMUNS}, power_steward, nivel_apuracao, unidade, "
        f"variaveis_utilizadas, memoria_calculo, restricoes, "
        f"dimensoes_negocio, decisao_negocio, "
        f"dimensao_tabelas, metrica_tabelas FROM {_cad('indicadores')} "
        f"ORDER BY nome"
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_permissoes() -> pd.DataFrame:
    return run_query(
        f"SELECT id, coalesce(nome, '') AS nome, email, papel, "
        f"coalesce(ver_cadastros,false) AS ver_cadastros, "
        f"coalesce(ver_logs,false) AS ver_logs, coalesce(aprovador_tags,false) AS aprovador_tags, "
        f"coalesce(ver_finops,false) AS ver_finops, "
        f"coalesce(power_steward,false) AS power_steward, "
        f"coalesce(engenharia,false) AS engenharia, "
        f"coalesce(admin_acesso,false) AS admin_acesso "
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


# ---------------------------------------------------------------------------
# Cadastro de Acesso a Dados (blueprint) — grupos, mapas, log de CRUD
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner=False)
def list_grupos() -> list[dict]:
    """``[{"id", "nome"}]`` dos grupos do workspace — só o rótulo.

    Os membros **não** são resolvidos aqui de propósito: (a) não escala em
    diretório grande (varreria users + SPs inteiros), (b) o SCIM do Databricks
    **não aceita** filtrar usuários por grupo (``groups.value eq`` → BadRequest).
    O usuário é escolhido por busca type-ahead (``buscar_principais``). Erro em
    ``st.session_state["_grupos_erro"]``.
    """
    st.session_state.pop("_grupos_erro", None)
    w = get_client(prefer_user=True)
    try:
        gs = [
            {"id": g.id, "nome": (g.display_name or g.id or "").strip()}
            for g in w.groups.list(attributes="id,displayName")
            if g.id
        ]
    except Exception as exc:
        st.session_state["_grupos_erro"] = f"groups.list: {type(exc).__name__}: {exc}"
        return []
    return sorted(gs, key=lambda d: d["nome"].lower())


@st.cache_data(ttl=120, show_spinner=False)
def buscar_principais(termo: str) -> list[dict]:
    """Type-ahead de usuário/SP por nome ou login, via filtro SCIM server-side
    (``co`` = contains) — sempre limitado, nunca varre o diretório (escala).

    ``[{"ident", "rotulo"}]``. ``ident`` é o que vai em ``mapa_*.usuario`` e o
    UDF ABAC casa com ``current_user()``: ``userName`` (pessoa) /
    ``applicationId`` (service principal).
    """
    termo = (termo or "").strip().replace('"', "")
    if len(termo) < 2:
        return []
    w = get_client(prefer_user=True)
    out: dict[str, str] = {}
    erros: list[str] = []

    def _add_users(campo: str) -> None:
        for u in w.users.list(
            filter=f'{campo} co "{termo}"',
            attributes="userName,displayName,active", count=25,
        ):
            ident = (u.user_name or "").strip()
            if ident and getattr(u, "active", True) is not False:
                out.setdefault(ident, (u.display_name or ident).strip())

    for campo in ("userName", "displayName"):
        try:
            _add_users(campo)
        except Exception as exc:
            erros.append(f"users({campo}): {type(exc).__name__}: {exc}")
    try:
        for s in w.service_principals.list(
            filter=f'displayName co "{termo}"',
            attributes="applicationId,displayName,active", count=25,
        ):
            ident = (s.application_id or "").strip()
            if ident and getattr(s, "active", True) is not False:
                out.setdefault(ident, (s.display_name or ident).strip())
    except Exception as exc:
        erros.append(f"service_principals: {type(exc).__name__}: {exc}")

    out.pop("", None)
    if erros and not out:
        st.session_state["_grupos_erro"] = " | ".join(erros)
    return sorted(
        [{"ident": i, "rotulo": r or i} for i, r in out.items()],
        key=lambda d: (d["rotulo"] or d["ident"]).lower(),
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_mapa_dominio_acesso() -> pd.DataFrame:
    return run_query(
        "SELECT id, grupo, grupo_id, usuario, dominio_id, subdominio_id, "
        "dominio, subdominio, criado_por, criado_em, atualizado_por, atualizado_em "
        f"FROM {_cad('mapa_dominio_acesso')} ORDER BY usuario, dominio"
    )


@st.cache_data(ttl=30, show_spinner=False)
def list_mapa_sensibilidade_acesso() -> pd.DataFrame:
    return run_query(
        "SELECT id, grupo, grupo_id, usuario, nivel_max_confidencialidade, "
        "pode_ver_dado_pessoal, pode_ver_dado_pessoal_sensivel, "
        "criado_por, criado_em, atualizado_por, atualizado_em "
        f"FROM {_cad('mapa_sensibilidade_acesso')} ORDER BY usuario"
    )


def _qn(value) -> str:
    """``q_str``, mas ``None`` vira ``NULL`` (colunas anuláveis)."""
    return "NULL" if value is None else q_str(str(value))


def _log_cadastro(usuario: str, tabela: str, operacao: str,
                  registro_id: str, antes: dict | None, depois: dict | None) -> None:
    """Registra um CRUD de cadastro (antes/depois em JSON). Best-effort —
    falha aqui nunca bloqueia a operação principal."""
    try:
        run_exec(
            f"INSERT INTO {_cad('log_cadastros')} "
            "(usuario, tabela, operacao, registro_id, antes, depois, criado_em) VALUES ("
            f"{q_str(usuario)}, {q_str(tabela)}, {q_str(operacao)}, {q_str(str(registro_id))}, "
            f"{_qn(json.dumps(antes, ensure_ascii=False, default=str) if antes else None)}, "
            f"{_qn(json.dumps(depois, ensure_ascii=False, default=str) if depois else None)}, "
            "current_timestamp())"
        )
    except Exception:
        pass


_NIVEIS_CONFIDENCIALIDADE = ["publico", "interno", "confidencial", "restrito"]


def _clear_cad_caches() -> None:
    for f in (
        list_franquias, list_dominios, list_subdominios, list_stewards, list_permissoes,
        list_dashboards, list_padroes_dado_pessoal, list_tag_backlog, get_user_perms,
        list_glossario_negocio, list_indicadores, list_termos_negocio, _novos_na_semana,
        list_mapa_dominio_acesso, list_mapa_sensibilidade_acesso, list_grupos,
        buscar_principais,
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


# ---------------------------------------------------------------------------
# Assistente de Governança (chat com IA) — opcional (LLM_ENABLED)
# ---------------------------------------------------------------------------
# Painel de chat com function-calling. SOMENTE LEITURA e SOMENTE METADADO:
# nenhuma tool roda SELECT em tabela (get_column_sample nao e exposto) e
# nenhuma tool de escrita e exposta ao modelo. As tools de metadado do
# Unity Catalog rodam OBO; as de dados internos do app rodam como o SP.
# Ver docs-produto/15-seguranca-assistente.md.

_DF_ROW_CAP = 200  # teto de linhas por resultado de tool, pra não estourar o contexto do modelo


def _df_records(df: pd.DataFrame, cap: int = _DF_ROW_CAP) -> list[dict]:
    return df.head(cap).to_dict("records")


ASSISTANT_SYSTEM_PROMPT = f"""Você é o assistente do {APP_NAME}, o app de governança de dados no Unity Catalog.

Você ajuda principalmente com DUAS coisas:""" + r"""

A) Achar as TABELAS e COLUNAS que servem para calcular um indicador. Os
   indicadores em geral já estão cadastrados no app (tool `termos_de_negocio`);
   o que o usuário costuma precisar é descobrir, nos metadados do Unity
   Catalog, quais colunas/tabelas usar como dimensão e como métrica e
   rascunhar a memória de cálculo.
   Fluxo: entenda o que ele quer medir -> `buscar_colunas` / `listar_schemas`
   / `listar_tabelas` para achar candidatas -> confirme com
   `tags_e_comentarios_da_tabela` -> proponha dimensão, métrica e a fórmula
   em texto. O cadastro em si ele faz na tela Cadastros → Indicador.

B) Responder QUEM é responsável por quê: o power steward de um indicador
   (campo `power_steward` em `termos_de_negocio`), o data owner/steward de um
   domínio ou sub-domínio (`data_stewards` + `dominios_e_subdominios`).

C) Mostrar ONDE há dado governado: "quais tabelas de X têm dado pessoal,
   dado pessoal sensível ou dado restrito", "onde a tag SOX está aplicada".
   Use `buscar_por_tag` (uma chamada por catálogo) — NÃO saia inspecionando
   tabela por tabela com `tags_e_comentarios_da_tabela`. Se o usuário não
   disser o catálogo, peça um. Nomes de tag úteis: `privacidade`,
   `seguranca`, `SOX`, `cliente`, `dominios_dados`, e as automáticas
   `class.*` (dado pessoal detectado automaticamente pelo Databricks) e
   `sap.PersonalData.*`.

Terminologia: use sempre "dado pessoal" e "dado pessoal sensível" — nunca a
sigla "PII".

Também responde outras perguntas sobre o que está registrado no app.

Limites — LEIA COM ATENÇÃO:
- Você só enxerga METADADOS (nome, tipo e comentário de coluna, tags
  governadas) e o que está cadastrado no app. Você NÃO tem acesso aos dados
  das tabelas. Nunca afirme valores, contagens, distribuições, exemplos de
  linha, min/max ou "como os dados se parecem" — se precisar disso, diga que
  o usuário deve olhar os dados na ferramenta de análise dele.
- Você é SOMENTE CONSULTA. Não aplica tag, não grava comentário, não cadastra
  nem edita indicador/termo, não aprova backlog. Oriente o usuário à tela
  certa do app.
- Use as ferramentas em vez de chutar. Se não houver ferramenta ou dado que
  cubra a pergunta, diga que não tem essa informação em vez de inventar.
- Se uma ferramenta devolver {"erro": "Sem permissão..."}, o usuário não tem
  acesso àquele dado no app — explique de forma breve e NÃO tente a mesma
  ferramenta de novo nem contorne por outra.
- Responda em português, de forma direta e objetiva.
"""

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "buscar_colunas",
            "description": (
                "Descoberta: procura colunas cujo NOME ou COMENTÁRIO contém um termo "
                "(ex.: 'faturamento', 'cliente', 'data'), varrendo os catálogos "
                "disponíveis. Devolve catálogo/schema/tabela/coluna/tipo/comentário — "
                "só metadados, nunca valores. Use para achar tabelas candidatas a "
                "dimensão/métrica de um indicador."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "termo": {"type": "string", "description": "Trecho a procurar em nome/comentário de coluna."},
                    "catalog": {"type": "string", "description": "Opcional: restringe a um catálogo."},
                },
                "required": ["termo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_schemas",
            "description": "Descoberta: schemas visíveis num catálogo.",
            "parameters": {
                "type": "object",
                "properties": {"catalog": {"type": "string"}},
                "required": ["catalog"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_tabelas",
            "description": "Descoberta: tabelas de um schema, com o comentário de cada uma.",
            "parameters": {
                "type": "object",
                "properties": {
                    "catalog": {"type": "string"},
                    "schema": {"type": "string"},
                },
                "required": ["catalog", "schema"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_por_tag",
            "description": (
                "Varre as tags governadas JÁ APLICADAS num catálogo (ou schema) "
                "inteiro, de uma vez — responde 'quais tabelas/colunas têm a tag X' "
                "ou 'onde há dado restrito/confidencial/pessoal/SOX'. Devolve "
                "catálogo/schema/tabela/coluna/tag/valor (coluna nula = tag na "
                "tabela). Só metadados, nunca valores de dado. Prefira esta tool a "
                "inspecionar tabela por tabela com tags_e_comentarios_da_tabela."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "catalog": {"type": "string", "description": "Catálogo a varrer (recomendado informar)."},
                    "schema": {"type": "string", "description": "Opcional: restringe a um schema."},
                    "tag_key": {"type": "string", "description": "Opcional: chave exata da tag (ex.: 'privacidade', 'SOX', 'class.br_cpf')."},
                    "tag_value": {"type": "string", "description": "Opcional: trecho do valor da tag (ex.: 'restrito', 'pessoal')."},
                },
            },
        },
    },
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


# Tools que espelham telas GENUINAMENTE restritas do app: o assistente não
# pode ser um caminho lateral para elas. Só o log de auditoria (histórico de
# mudanças) e o backlog de aprovação (solicitações + justificativas) entram
# aqui — o resto (domínios, stewards, dashboards, padrões, glossário, tags
# governadas, metadados de tabela) é dado de referência/contato, útil para
# qualquer usuário e já visível de forma equivalente na tela pública de
# consulta ao glossário e nos contadores da tela de Início.
_TOOL_REQUIRED_PERM = {
    "backlog_de_aprovacao_de_tags": "aprovador_tags",
    "log_auditoria": "ver_logs",
}


def _tool_allowed(name: str) -> bool:
    """True se o papel/flags do usuário logado liberam esta tool."""
    required = _TOOL_REQUIRED_PERM.get(name)
    if not required:
        return True
    perms = st.session_state.get("perms", {}) or {}
    if perms.get("papel") == "admin":
        return True
    return bool(perms.get(required))


def _execute_tool(name: str, args: dict, user: str) -> dict:
    """Executa uma tool e devolve um dict serializável (lista de records ou erro)."""
    if not _tool_allowed(name):
        return {"erro": (
            "Sem permissão: seu perfil no app não tem acesso a esses dados "
            "(a tela correspondente também ficaria oculta). Fale com um "
            "administrador se precisar dessa informação."
        )}
    try:
        if name == "tags_e_comentarios_da_tabela":
            catalog, schema, table = args["catalog"], args["schema"], args["table"]
            if ALLOWED_CATALOGS and str(catalog).lower() not in ALLOWED_CATALOGS:
                return {"erro": f"O catálogo '{catalog}' não está disponível neste app."}
            # Fail-closed: se o app roda em modo OBO mas o token do usuário não
            # chegou, NÃO cair para o service principal (que enxerga mais que o
            # usuário). Sem identidade do usuário, não respondemos metadados.
            if USE_ON_BEHALF_OF_USER and not _forwarded_user_token():
                return {"erro": (
                    "Não consegui confirmar sua identidade (token de usuário "
                    "ausente) — recarregue o app e aceite o consentimento de "
                    "acesso antes de consultar metadados de tabela."
                )}
            # Mesmo portão das escritas (user_can_access_table, via OBO): só
            # respondemos sobre tabelas que o usuário logado de fato enxerga.
            if not user_can_access_table(user, catalog, schema, table):
                return {"erro": (
                    f"Você não tem acesso à tabela {catalog}.{schema}.{table} "
                    "— não posso mostrar os metadados dela."
                )}
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
        if name in ("buscar_colunas", "listar_schemas", "listar_tabelas", "buscar_por_tag"):
            # Descoberta de metadados — roda sob OBO. Mesmo fail-closed do
            # tags_e_comentarios_da_tabela: sem token do usuário, não usamos SP.
            if USE_ON_BEHALF_OF_USER and not _forwarded_user_token():
                return {"erro": (
                    "Não consegui confirmar sua identidade (token de usuário "
                    "ausente) — recarregue o app e aceite o consentimento de acesso."
                )}
            cat = args.get("catalog")
            if cat and ALLOWED_CATALOGS and str(cat).lower() not in ALLOWED_CATALOGS:
                return {"erro": f"O catálogo '{cat}' não está disponível neste app."}
            if name == "buscar_colunas":
                return {"colunas": search_columns(user, args.get("termo", ""), cat)}
            if name == "buscar_por_tag":
                return {"tags_aplicadas": search_by_tag(
                    user, cat, args.get("schema"), args.get("tag_key"), args.get("tag_value"),
                )}
            catalog = args["catalog"]
            if name == "listar_schemas":
                return {"schemas": list_schemas(user, catalog)}
            return {"tabelas": list_tables_with_comment(user, catalog, args["schema"])}
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


def run_assistant_turn(user_text: str, user: str) -> str:
    """Processa uma pergunta do usuário no painel do assistente e devolve a resposta final.

    Faz o loop de tool-calling localmente (até MAX_ITERATIONS idas e vindas);
    só a mensagem final do assistente é persistida no histórico da sessão —
    as mensagens de tool call ficam só dentro deste loop.
    """
    # Montar um indicador é exploratório (buscar colunas -> listar tabelas ->
    # inspecionar algumas) e consome várias idas e vindas — 6 era pouco.
    MAX_ITERATIONS = 10
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
                max_tokens=2500,
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
    """Painel do assistente ancorado à direita, recolhível para uma aba fina.
    Começa recolhido (aba fina) — o usuário abre quando quiser."""
    if not st.session_state.get("show_assistant", False):
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
        sugestoes = [
            "Que colunas e tabelas posso usar para calcular um indicador de margem?",
            "Quais tabelas de um catálogo têm dado pessoal ou restrito?",
            "Quais indicadores já estão cadastrados e quem é o power steward de cada um?",
            "Quem são os data stewards e de qual domínio cada um cuida?",
        ]
        with st.expander("💡 Sugestões", expanded=True):
            for label in sugestoes:
                if st.button(label, use_container_width=True, key=f"assist_qp_{label}"):
                    _ask(label)

    prompt = st.chat_input("Pergunte ao assistente…")
    if prompt:
        _ask(prompt)



# ---------------------------------------------------------------------------
# Pipeline de publicação (blueprint seção 5.1) — Passo 1
# ---------------------------------------------------------------------------


def gerar_expr_sql(formula_texto: str, measures_colunas: list[str]) -> dict:
    """Traduz a fórmula de um indicador (linguagem natural) numa expressão SQL
    candidata, via `assistente_governanca` (Unity AI Gateway).

    `measures_colunas` é a lista real de colunas de origem (vinda do lineage
    em `metrica_tabelas[].colunas`) — o prompt instrui o modelo a usar
    exclusivamente esses nomes, pra evitar alucinação de coluna inexistente.

    Não executa a expressão nem toca em dado real — isso é o Passo 2
    (`testar_candidato`, ainda não implementado). Retorna
    ``{"expr_sql": str, "explicacao": str}``. Propaga exceções de conexão/
    parsing pra quem chamar decidir como exibir o erro.
    """
    if not measures_colunas:
        raise ValueError("measures_colunas não pode ser vazio — sem lineage não há o que traduzir.")

    colunas_fmt = ", ".join(f"`{c}`" for c in measures_colunas)
    prompt = (
        "Você é um tradutor de fórmulas de indicadores de negócio para SQL "
        "(dialeto Databricks/Spark SQL), usado num pipeline de governança de dados.\n\n"
        f"Colunas reais disponíveis — use SOMENTE estas, exatamente como estão "
        f"escritas, nunca invente nomes de coluna: {colunas_fmt}\n\n"
        f'Fórmula em linguagem natural: "{formula_texto}"\n\n'
        "Responda em JSON puro, sem markdown e sem texto fora do JSON, neste formato exato:\n"
        '{"expr_sql": "<expressão SQL de agregação, ex: SUM(col_a) / SUM(col_b)>", '
        '"explicacao": "<reformulação em português do que foi entendido>"}'
    )
    client = get_llm_client()
    response = client.chat.completions.create(
        model=LLM_ENDPOINT,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=400,
    )
    texto = _extract_text(response.choices[0].message.content).strip()
    # Alguns modelos devolvem o JSON dentro de um bloco ```json ... ``` mesmo
    # quando instruídos a não fazer isso — extrai só o trecho entre chaves.
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fim == -1:
        raise ValueError(f"Resposta do assistente não trouxe JSON reconhecível: {texto!r}")
    dados = json.loads(texto[inicio:fim + 1])
    expr_sql = (dados.get("expr_sql") or "").strip()
    explicacao = (dados.get("explicacao") or "").strip()
    if not expr_sql:
        raise ValueError(f"Assistente não retornou 'expr_sql': {texto!r}")
    return {"expr_sql": expr_sql, "explicacao": explicacao}


# ---------------------------------------------------------------------------
# Pipeline de publicação (blueprint seção 5.1) — Passos 2, 4, 5
# ---------------------------------------------------------------------------

# Palavras/sinais que não podem aparecer numa expressão candidata gerada por
# IA — defesa em profundidade contra injeção de SQL via prompt. A expressão
# roda como SELECT contra dado real (com o Service Principal) em
# `testar_candidato`, ANTES da confirmação humana do Passo 3.
_SQL_PALAVRAS_PROIBIDAS = re.compile(
    r";|--|/\*|\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|GRANT|REVOKE|"
    r"MERGE|TRUNCATE|EXEC|EXECUTE|CALL)\b",
    re.IGNORECASE,
)


def _validar_expr_sql_segura(expr_sql: str) -> None:
    """Levanta ValueError se a expressão candidata tiver qualquer sinal de
    comando fora de uma expressão de agregação simples (ponto e vírgula,
    comentário SQL, ou palavra-chave de DDL/DML)."""
    if _SQL_PALAVRAS_PROIBIDAS.search(expr_sql):
        raise ValueError("Expressão candidata contém comando não permitido — revise antes de testar.")


def testar_candidato(expr_sql: str, catalogo: str, schema: str, tabela: str) -> float | None:
    """Executa a expressão candidata como agregação real contra a tabela de
    origem (Passo 2) e devolve o valor numérico resultante. Não salva nada —
    é só o "preview" que embasa a confirmação humana do Passo 3.

    Roda via `run_query(prefer_user=True)` — com `USE_ON_BEHALF_OF_USER=true`
    isso vira OBO de verdade: precisa que o usuário logado tenha `SELECT`
    real na tabela, não só ter conseguido listá-la no picker. Hoje
    (`USE_ON_BEHALF_OF_USER=false`, ver `app.yaml`) ainda cai pro Service
    Principal — mesma limitação que levou à remoção do assistente de chat
    (ver comentário em `LLM_ENABLED`), pendente de resolver o problema do
    escopo `sql` antes de religar. O `prefer_user=True` já fica pronto pra
    quando isso for religado, pra não esquecer de novo.
    """
    _validar_expr_sql_segura(expr_sql)
    tabela_fqn = q_full(catalogo, schema, tabela)
    df = run_query(f"SELECT {expr_sql} AS resultado FROM {tabela_fqn}", prefer_user=True)
    if df.empty:
        raise RuntimeError("A consulta de teste não retornou nenhuma linha.")
    valor = df.iloc[0, 0]
    return None if valor is None else float(valor)


def _slugify(nome: str) -> str:
    """Nome do indicador → identificador SQL seguro (nome de measure/view):
    minúsculo, sem acento, espaço/pontuação vira `_`, sem `_` duplicado nem
    nas pontas."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")
    return slug or "indicador"


def _montar_comentario_metric_view(indicador: dict) -> str:
    """Concatena Objetivo + Decisão apoiada (campos de negócio do Indicador)
    num único comentário pra Metric View — é a "operacionalização" pedida:
    o negócio descreve os dois campos separadamente na tela Indicador (o
    Objetivo já existia; Decisão apoiada é o novo campo, `decisao_negocio`),
    e aqui, na publicação, eles viram uma frase só, sem exigir nenhuma
    decisão extra de quem cadastra. Sem aspas/quebra de linha — vai dentro
    do `CREATE VIEW ... $$...$$` do YAML."""
    objetivo = (indicador.get("objetivo") or "").strip()
    decisao = (indicador.get("decisao_negocio") or "").strip()
    partes = [objetivo] + ([f"Decisão apoiada: {decisao}"] if decisao else [])
    return " — ".join(partes).replace('"', "'").replace("\n", " ").strip()


def montar_yaml_metric_view(indicador: dict) -> str:
    """Monta o YAML da Metric View (Passo 4) só transcrevendo campos já
    estruturados do indicador — determinístico, sem IA envolvida. Exige
    lineage de métrica e `expr_validada` já preenchidos (Passo 3 confirmado);
    dimensão é opcional.
    """
    met_items = _parse_tabelas_json(indicador.get("metrica_tabelas"))
    if not met_items:
        raise ValueError("Indicador sem lineage de métrica — cadastre a tabela/colunas antes.")
    expr_validada = (indicador.get("expr_validada") or "").strip()
    if not expr_validada:
        raise ValueError("Indicador sem expressão validada — confirme o Passo 3 antes.")

    fonte = met_items[0]
    catalogo, schema, tabela = fonte["catalogo"], fonte["schema"], fonte["tabela"]

    dim_items = _parse_tabelas_json(indicador.get("dimensao_tabelas"))
    if dim_items:
        fonte_dim = dim_items[0]
        if (fonte_dim["catalogo"], fonte_dim["schema"], fonte_dim["tabela"]) != (catalogo, schema, tabela):
            raise ValueError(
                "Dimensão e métrica apontam para tabelas diferentes — "
                "ajuste o lineage do indicador antes de publicar."
            )

    dim_cols: list[str] = []
    for it in dim_items:
        for col in it.get("colunas") or []:
            if col not in dim_cols:
                dim_cols.append(col)

    nome_medida = _slugify(indicador["nome"])
    comment = _montar_comentario_metric_view(indicador)
    synonyms = [s.strip() for s in (indicador.get("palavras_chave") or "").split(",") if s.strip()]

    linhas = ["version: 1.1", f"source: {catalogo}.{schema}.{tabela}"]
    if dim_cols:
        linhas.append("dimensions:")
        for col in dim_cols:
            # `name` precisa ser um identificador SQL válido (slugify); `expr`
            # referencia a coluna real, com backtick (sem isso, um nome de
            # coluna com espaço/acento — ex.: "Data da Venda" — quebra o SQL
            # dentro do YAML: PARSE_SYNTAX_ERROR, confirmado). O valor do
            # `expr` precisa ir entre aspas duplas no YAML — sem isso, o
            # parser de YAML do Metric View rejeita a linha porque um valor
            # não pode COMEÇAR com backtick fora de uma string (também
            # confirmado: "found character '`' that cannot start any token").
            linhas.append(f"  - name: {_slugify(col)}")
            linhas.append(f'    expr: "`{col}`"')
    linhas.append("measures:")
    linhas.append(f"  - name: {nome_medida}")
    linhas.append(f"    expr: {expr_validada}")
    if comment:
        linhas.append(f'    comment: "{comment}"')
    if synonyms:
        syn_fmt = ", ".join(f'"{s}"' for s in synonyms)
        linhas.append(f"    synonyms: [{syn_fmt}]")
    return "\n".join(linhas)


def montar_ddl_metric_view(indicador: dict) -> tuple[str, str]:
    """Monta o DDL completo (`CREATE OR REPLACE VIEW ... WITH METRICS
    LANGUAGE YAML`) do indicador (Passo 5) — só transcreve/monta texto,
    **não executa nada**. Decisão de arquitetura: publicar (rodar o DDL de
    verdade) é ação de quem tem `CREATE`/`MODIFY` no schema de destino, não
    do Service Principal do app — a Engenharia copia o SQL daqui e roda onde
    achar melhor (SQL Editor, notebook, outro workspace…), sob a própria
    identidade. Isso também tira do app a necessidade de `CREATE`/`MODIFY`
    em todo schema onde um indicador possa vir a ser publicado.

    Só exige ``status_publicacao == 'validado'`` — quem chama é responsável
    por já ter checado isso (a UI do Passo 4 só mostra o DDL nesse estado).

    Retorna ``(ddl_sql, view_fqn)`` — o DDL pronto pra copiar e o nome
    totalmente qualificado (sem quoting) sugerido pra view.
    """
    if indicador.get("status_publicacao") != "validado":
        raise ValueError("Indicador precisa estar com status 'validado' antes de gerar o DDL.")
    yaml_txt = montar_yaml_metric_view(indicador)
    if "$$" in yaml_txt:
        # Delimitador do CREATE VIEW ... AS $$...$$ — não deveria acontecer
        # (nome/objetivo/synonyms não deveriam conter isso), mas confere
        # antes de montar o DDL em vez de deixar o Spark SQL falhar feio.
        raise ValueError("YAML gerado contém '$$', incompatível com o delimitador do CREATE VIEW.")
    met_items = _parse_tabelas_json(indicador.get("metrica_tabelas"))
    catalogo, schema = met_items[0]["catalogo"], met_items[0]["schema"]
    nome_view = _slugify(indicador["nome"])
    view_fqn = q_full(catalogo, schema, nome_view)
    ddl_sql = f"CREATE OR REPLACE VIEW {view_fqn} WITH METRICS LANGUAGE YAML AS $$\n{yaml_txt}\n$$"
    return ddl_sql, f"{catalogo}.{schema}.{nome_view}"


# ---------------------------------------------------------------------------
# FinOps (blueprint seção 8.2) — Passo 1
# ---------------------------------------------------------------------------
# Só a função de query por enquanto — cards, gráfico e tabela (Passos 2-4 da
# seção 8.2) ainda não foram construídos. Ver finopsmockup.html pro layout
# de referência (não implementado ainda, só pra saber onde isso está indo).


def _q_date(d: date) -> str:
    """Literal DATE seguro pro Spark SQL — só aceita `date`/`datetime` de
    verdade (não string), então não tem o que injetar."""
    if not isinstance(d, date):
        raise TypeError(f"Esperava um date, recebi {type(d).__name__}")
    return f"DATE'{d.isoformat()}'"


def obter_custo_por_dominio(data_inicio: date, data_fim: date) -> pd.DataFrame:
    """Custo (USD) e DBUs consumidos no período, por dia/domínio/tipo de
    custo (blueprint seção 8.2, Passo 1 — query "final", com DBUs além do
    valor em USD, pra auditoria técnica independente de preço).

    O domínio vem da custom tag `domain` aplicada no warehouse/compute
    (seção 8.1, Passo 0 — convenção manual, não código); recursos sem essa
    tag caem em `sem_tag`. `tipo_custo` distingue:
      - "Compute do App"  — fixo, roda enquanto o App estiver ACTIVE
      - "SQL Warehouse"   — variável
      - "IA (assistente + pipeline)" — variável; chamadas ao AI Gateway
        (assistente de chat) e ao model service (tradução de fórmula no
        pipeline de indicador). Domínio fixo "IA / governança".
      - "Outro"

    Escopo do "custo do Power Steward": `usage_metadata.app_id` deste App
    (`DATABRICKS_CLIENT_ID`) OU `usage_metadata.warehouse_id` (`WAREHOUSE_ID`)
    OU as linhas de IA — `billing_origin_product = 'AI_GATEWAY'` com
    `endpoint_name = LLM_ENDPOINT`, e `billing_origin_product = 'MODEL_SERVING'`
    com `identity_metadata.run_as = <SP do app>` (a IA não carrega `app_id`).
    Sem esse filtro a query somaria o billing da conta inteira. Limitação
    conhecida: o warehouse compartilhado entre Apps (Free Edition libera 1)
    faz a fatia "SQL Warehouse" incluir uso de outros Apps — resolve com
    warehouse dedicado (seção 4).

    Lê `system.billing.usage`/`system.billing.list_prices` — tabelas de
    sistema do Unity Catalog, não dado interno do app — por isso pede OBO
    (`prefer_user=True`), igual à regra geral de leitura de catálogo (ver
    docstring do módulo).

    ⚠️ `system.billing` só concede `SELECT`/`USE SCHEMA` ao grupo reservado
    `account admins` — nem um metastore admin comum consegue dar `GRANT`
    nesse schema pro Service Principal (testado: `PERMISSION_DENIED: User
    does not have MANAGE on Schema 'system.billing'`, mesmo sendo o dono da
    conta Free Edition). Ou seja, com `USE_ON_BEHALF_OF_USER=false` (estado
    atual, ver `app.yaml`) esta função **não funciona de jeito nenhum** — cai
    pro SP, que nunca vai ter acesso a `system.billing` aqui. Só funciona com
    OBO de verdade (usando o token do usuário, que pode ter esse acesso
    pessoalmente). Pendência bloqueadora: religar OBO esbarra no erro de
    escopo `sql` (ver comentário em `LLM_ENABLED`). O controle de acesso *por
    domínio* dentro da página (seção 8.1, Passo 5) também ainda não foi
    implementado.
    """
    this_app_id = os.environ.get("DATABRICKS_CLIENT_ID", "").strip()
    escopo = []
    if this_app_id:
        escopo.append(f"u.usage_metadata.app_id = {q_str(this_app_id)}")
    if WAREHOUSE_ID:
        escopo.append(f"u.usage_metadata.warehouse_id = {q_str(WAREHOUSE_ID)}")
    # Custo de IA do Power Steward (assistente de chat + tradução de fórmula no
    # pipeline de publicação). NÃO carrega `usage_metadata.app_id` — a chave de
    # atribuição é o `endpoint_name` (roteamento no AI Gateway) e o
    # `identity_metadata.run_as` (a inferência de fato, feita com o SP do app).
    # Ambos os caminhos usam LLM_ENDPOINT / o SP, então "IA" agrega os dois.
    if LLM_ENDPOINT:
        escopo.append(
            f"(u.billing_origin_product = 'AI_GATEWAY' "
            f"AND u.usage_metadata.endpoint_name = {q_str(LLM_ENDPOINT)})"
        )
    if this_app_id:
        escopo.append(
            f"(u.billing_origin_product = 'MODEL_SERVING' "
            f"AND u.identity_metadata.run_as = {q_str(this_app_id)})"
        )
    # Sem nenhum critério de escopo (ex.: rodando fora do runtime do App) não dá
    # pra escopar — melhor devolver tudo (comportamento antigo) do que uma
    # cláusula `WHERE ... AND ()` inválida.
    filtro_escopo = f"AND ({' OR '.join(escopo)})" if escopo else ""
    sql = f"""
        SELECT
          DATE(u.usage_date) AS dia,
          CASE
            WHEN u.billing_origin_product IN ('AI_GATEWAY', 'MODEL_SERVING') THEN 'IA / governança'
            ELSE COALESCE(u.custom_tags['domain'], 'sem_tag')
          END AS dominio,
          CASE
            WHEN u.billing_origin_product IN ('AI_GATEWAY', 'MODEL_SERVING') THEN 'IA (assistente + pipeline)'
            WHEN u.usage_metadata.app_id IS NOT NULL THEN 'Compute do App'
            WHEN u.usage_metadata.warehouse_id IS NOT NULL THEN 'SQL Warehouse'
            ELSE 'Outro'
          END AS tipo_custo,
          SUM(u.usage_quantity) AS dbus,
          SUM(u.usage_quantity * p.pricing.default) AS custo_usd
        FROM system.billing.usage u
        JOIN system.billing.list_prices p
          ON u.sku_name = p.sku_name
          AND u.usage_date BETWEEN p.price_start_time AND COALESCE(p.price_end_time, current_date())
        WHERE u.usage_date BETWEEN {_q_date(data_inicio)} AND {_q_date(data_fim)}
        {filtro_escopo}
        GROUP BY dia, dominio, tipo_custo
        ORDER BY dia
    """
    return run_query(sql, prefer_user=True)


# Rótulo do "tipo_custo" que representa custo fixo (compute do app, sempre
# ligado enquanto o App estiver ACTIVE). Todo o resto (SQL Warehouse + Outro)
# entra como variável na composição — ver `page_finops`.
_FINOPS_TIPO_FIXO = "Compute do App"

_FINOPS_PERIODOS = {"Últimos 7 dias": 7, "Últimos 30 dias": 30, "Últimos 90 dias": 90}

# Fallback estático pra quando o OBO não estiver disponível (ver comentário
# em LLM_ENABLED — o token do usuário vem sem o escopo `sql` no Free Edition
# desta POC, e o Service Principal não tem — e não pode ter — acesso a
# `system.billing`). Dado REAL, gerado rodando a mesma query do Passo 1 com
# credencial pessoal (que tem acesso), fora do app — não é número inventado,
# só não está "ao vivo". Arquivo versionado junto do app.py.
_FINOPS_EXCEL_FALLBACK = "finops_dados_demo.xlsx"


def _carregar_finops_excel() -> pd.DataFrame | None:
    """Lê o snapshot estático (aba `custo_por_dominio`). None se o arquivo
    não existir no deploy (não é erro — só significa que não há fallback)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _FINOPS_EXCEL_FALLBACK)
    if not os.path.exists(path):
        return None
    df = pd.read_excel(path, sheet_name="custo_por_dominio")
    df["dia"] = pd.to_datetime(df["dia"]).dt.date
    return df


def _carregar_finops_snapshot() -> pd.DataFrame | None:
    """Lê o snapshot de FinOps de ``FINOPS_SNAPSHOT_TABLE`` (Delta), como SP.

    É o caminho para ambientes onde o SP não pode ler ``system.billing`` mas um
    job externo mantém uma tabela agregada. ``None`` se a env var não estiver
    configurada, a tabela não existir/estiver vazia, ou faltar grant — nunca
    levanta (cai pro xlsx demo)."""
    if not FINOPS_SNAPSHOT_TABLE:
        return None
    try:
        df = run_query(
            "SELECT dia, dominio, tipo_custo, dbus, custo_usd "
            f"FROM {q_fqn(FINOPS_SNAPSHOT_TABLE)}"
        )
    except Exception:
        return None
    if df.empty:
        return None
    df["dia"] = pd.to_datetime(df["dia"]).dt.date
    df["dbus"] = pd.to_numeric(df["dbus"], errors="coerce")
    df["custo_usd"] = pd.to_numeric(df["custo_usd"], errors="coerce")
    return df


def _render_finops_dashboard(df: pd.DataFrame) -> None:
    """Cards + gráfico + tabela (blueprint seção 8.2, Passos 2-4) — só
    renderiza a partir de um DataFrame já no formato de
    `obter_custo_por_dominio` (dia, dominio, tipo_custo, dbus, custo_usd).
    Usado tanto pro dado ao vivo quanto pelo fallback estático."""
    df = df.copy()
    # Normaliza tipos: o caminho ao vivo (Statement Execution API) devolve
    # tudo como string; o fallback do Excel já vem com tipos nativos. A
    # partir daqui o resto do código pode assumir date/float de verdade.
    df["dia"] = pd.to_datetime(df["dia"]).dt.date
    df["custo_usd"] = df["custo_usd"].astype(float)
    df["dbus"] = df["dbus"].astype(float)
    hoje = date.today()

    custo_total = float(df["custo_usd"].sum())
    dbus_total = float(df["dbus"].sum())
    custo_fixo = float(df.loc[df["tipo_custo"] == _FINOPS_TIPO_FIXO, "custo_usd"].sum())
    pct_fixo = (custo_fixo / custo_total * 100) if custo_total else 0.0
    dias_com_dado = df["dia"].nunique() or 1
    media_diaria = custo_total / dias_com_dado
    # Projeção pelo dias-do-mês-corrente (não um "× 30" fixo) — mais
    # defensável se questionado, por não superestimar/subestimar em meses
    # curtos/longos (blueprint seção 8.2, item 3).
    dias_no_mes = calendar.monthrange(hoje.year, hoje.month)[1]
    projecao_mes = media_diaria * dias_no_mes

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Custo total no período", f"US$ {custo_total:,.2f}")
    c2.metric("Fixo vs. variável", f"{pct_fixo:.0f}% / {100 - pct_fixo:.0f}%")
    c3.metric("Custo médio / dia", f"US$ {media_diaria:,.2f}")
    c4.metric("Projeção do mês corrente", f"≈ US$ {projecao_mes:,.2f}")
    c5.metric("DBUs no período", f"{dbus_total:,.2f}")
    st.caption(f"Fixo = {_FINOPS_TIPO_FIXO} · Variável = SQL Warehouse + IA + Outro")

    st.markdown("#### Evolução diária — por tipo de custo")
    pivot = df.pivot_table(
        index="dia", columns="tipo_custo", values="custo_usd", aggfunc="sum", fill_value=0.0,
    )
    # Índice vira rótulo "DD/MM" (string) em vez de `date` — o eixo do
    # gráfico passa a ser categórico (uma coluna por dia, coladas, sem tick
    # de hora) em vez de escala temporal contínua. Ordenação por `date` já
    # aconteceu no pivot acima (sort=True padrão), antes da troca pra string
    # — então continua cronológico mesmo virando texto.
    pivot.index = [d.strftime("%d/%m") for d in pivot.index]
    st.bar_chart(pivot, stack=True)

    st.markdown("#### Custo por domínio")
    por_dominio = (
        df.groupby("dominio", as_index=False)
        .agg(dbus=("dbus", "sum"), custo_usd=("custo_usd", "sum"))
        .sort_values("custo_usd", ascending=False)
    )
    por_dominio["pct"] = (por_dominio["custo_usd"] / custo_total * 100) if custo_total else 0.0
    st.dataframe(
        por_dominio.rename(columns={
            "dominio": "Domínio", "dbus": "DBUs no período",
            "custo_usd": "Custo (USD)", "pct": "% do total",
        }).style.format({"DBUs no período": "{:.2f}", "Custo (USD)": "US$ {:.2f}", "% do total": "{:.1f}%"}),
        use_container_width=True, hide_index=True,
    )
    if por_dominio["dominio"].eq("sem_tag").any():
        st.caption("`sem_tag` = recursos ainda sem tag de domínio aplicada.")

    dia_min, dia_max = df["dia"].min(), df["dia"].max()
    st.caption(
        f"Dados de billing de {dia_min.strftime('%d/%m/%Y')} a {dia_max.strftime('%d/%m/%Y')} — "
        "o Databricks atualiza `system.billing.usage` com atraso de algumas horas; "
        "este painel não é em tempo real."
    )


def page_finops() -> None:
    """Página de FinOps. Fontes de custo, em ordem: (1) `system.billing` ao vivo
    via OBO; (2) `FINOPS_SNAPSHOT_TABLE` — tabela mantida por um job externo,
    lida como SP, pra ambientes onde o SP não pode ler `system.billing`;
    (3) `_FINOPS_EXCEL_FALLBACK` — snapshot estático de demo. A troca é
    silenciosa (não expõe o motivo técnico na tela)."""
    st.title("💰 FinOps — Custo da Governança")
    st.caption(
        f"Custo do {APP_NAME}: Compute do App + SQL Warehouse + IA "
        "(assistente e tradução de fórmula de indicador)."
    )

    periodo_label = st.selectbox("Período", options=list(_FINOPS_PERIODOS.keys()), index=1)
    dias = _FINOPS_PERIODOS[periodo_label]
    hoje = date.today()
    data_inicio = hoje - timedelta(days=dias - 1)

    df: pd.DataFrame | None = None
    try:
        df_vivo = obter_custo_por_dominio(data_inicio, hoje)
        if not df_vivo.empty:
            df = df_vivo
    except Exception:
        pass

    if df is None:
        # Fontes não-live: a tabela de snapshot (job externo) ou o xlsx de demo.
        # Ambas trazem histórico — o recorte por período acontece aqui.
        base = _carregar_finops_snapshot()
        if base is None or base.empty:
            base = _carregar_finops_excel()
        if base is None or base.empty:
            st.info("Nenhum dado de custo disponível no momento.")
            return
        recorte = base[(base["dia"] >= data_inicio) & (base["dia"] <= hoje)]
        df = recorte if not recorte.empty else base

    _render_finops_dashboard(df)


# ---------------------------------------------------------------------------
# Páginas de cadastro
# ---------------------------------------------------------------------------


_HIER_NOVA_FR = "➕ nova franquia…"
_HIER_NOVO_DOM = "➕ novo domínio…"
_HIER_NOVO_SUB = "➕ novo sub-domínio…"
_HIER_NENHUM = "— (nenhum)"
_HIER_SEM_FR = "(sem franquia — a vincular)"


def page_dominios() -> None:
    st.title("🗂️ Domínios")
    st.caption(
        "Hierarquia de negócio em **três níveis: Franquia › Domínio › Sub-domínio**. "
        "Domínios, Data Owners/Stewards, dashboards e indicadores se vinculam a essa "
        "árvore. Um formulário só monta e edita a árvore inteira."
    )
    _show_cad_feedback()
    role = st.session_state.get("role", "leitor")
    user = st.session_state.get("user", "")
    somente_leitura = not can_edit(role)

    frs = list_franquias().to_dict("records")
    doms = list_dominios().to_dict("records")
    subs = list_subdominios().to_dict("records")

    _render_arvore_hierarquia(frs, doms, subs)

    if somente_leitura:
        st.info("Seu perfil é **leitor** — visualização apenas.")
        return

    st.divider()
    st.markdown("#### Adicionar / editar")
    _form_hierarquia(frs, doms, subs, user)


def _hier_franquia_id(d: dict):
    """``franquia_id`` de um registro de domínio como int, ou ``None``."""
    v = d.get("franquia_id")
    return int(v) if v is not None and pd.notna(v) else None


def _render_arvore_hierarquia(frs: list[dict], doms: list[dict], subs: list[dict]) -> None:
    """Árvore Franquia › Domínio › Sub-domínio (somente leitura)."""
    if not frs and not doms:
        st.info("Nada cadastrado ainda. Use o formulário abaixo para criar a primeira franquia.")
        return

    subs_por_dom: dict = {}
    for s in subs:
        subs_por_dom.setdefault(int(s["dominio_id"]), []).append(s)
    doms_por_fr: dict = {}
    orfaos: list[dict] = []
    for d in doms:
        fid = _hier_franquia_id(d)
        if fid is None:
            orfaos.append(d)
        else:
            doms_por_fr.setdefault(fid, []).append(d)

    def _rot(icone: str, nome: str, desc) -> str:
        txt = f" — _{desc}_" if desc else ""
        return f"{icone} **{nome}**{txt}" if icone == "🏙️" else f"{icone} {nome}{txt}"

    _chave = lambda x: (x.get("nome") or "").lower()
    linhas: list[str] = []
    for f in sorted(frs, key=_chave):
        linhas.append("- " + _rot("🏙️", f["nome"], f.get("descricao")))
        f_doms = sorted(doms_por_fr.get(int(f["id"]), []), key=_chave)
        if not f_doms:
            linhas.append("    - _(sem domínios)_")
        for d in f_doms:
            linhas.append("    - " + _rot("🗂️", d["nome"], d.get("descricao")))
            for s in sorted(subs_por_dom.get(int(d["id"]), []), key=_chave):
                linhas.append("        - " + _rot("🗃️", s["nome"], s.get("descricao")))
    if linhas:
        st.markdown("\n".join(linhas))

    if orfaos:
        st.warning("**Domínios sem franquia** — edite cada um no formulário para vincular:")
        ol: list[str] = []
        for d in sorted(orfaos, key=_chave):
            ol.append("- " + _rot("🗂️", d["nome"], d.get("descricao")))
            for s in sorted(subs_por_dom.get(int(d["id"]), []), key=_chave):
                ol.append("    - " + _rot("🗃️", s["nome"], s.get("descricao")))
        st.markdown("\n".join(ol))


def _form_hierarquia(frs: list[dict], doms: list[dict], subs: list[dict], user: str) -> None:
    """Formulário único em cascata: cria/edita qualquer nível da árvore."""
    fr_por_nome = {f["nome"]: f for f in frs}
    tem_orfaos = any(_hier_franquia_id(d) is None for d in doms)
    fr_opts = sorted(fr_por_nome)
    if tem_orfaos:
        fr_opts.append(_HIER_SEM_FR)
    fr_opts.append(_HIER_NOVA_FR)
    sel_fr = st.selectbox("Franquia *", options=fr_opts, key="hier_fr")
    criando_fr = sel_fr == _HIER_NOVA_FR
    sem_fr = sel_fr == _HIER_SEM_FR
    fr_atual = fr_por_nome.get(sel_fr) if not (criando_fr or sem_fr) else None
    fr_id = int(fr_atual["id"]) if fr_atual else None
    novo_fr_nome = st.text_input("Nome da nova franquia *", key="hier_fr_novo") if criando_fr else ""

    if criando_fr:
        dom_da_fr: list[dict] = []
    elif sem_fr:
        dom_da_fr = sorted(
            [d for d in doms if _hier_franquia_id(d) is None],
            key=lambda x: (x.get("nome") or "").lower(),
        )
    else:
        dom_da_fr = sorted(
            [d for d in doms if _hier_franquia_id(d) == fr_id],
            key=lambda x: (x.get("nome") or "").lower(),
        )
    dom_por_nome = {d["nome"]: d for d in dom_da_fr}
    dom_opts = [_HIER_NENHUM] + list(dom_por_nome)
    if not sem_fr:  # criar domínio só sob franquia real/nova
        dom_opts.append(_HIER_NOVO_DOM)
    sel_dom = st.selectbox(
        "Domínio", options=dom_opts, key=f"hier_dom::{sel_fr}",
        help=("Selecione um domínio sem franquia para vinculá-lo ou excluí-lo."
              if sem_fr else "Deixe em “(nenhum)” para criar ou editar apenas a franquia."),
    )
    criando_dom = sel_dom == _HIER_NOVO_DOM
    dom_atual = dom_por_nome.get(sel_dom) if sel_dom not in (_HIER_NENHUM, _HIER_NOVO_DOM) else None
    dom_id = int(dom_atual["id"]) if dom_atual else None
    novo_dom_nome = st.text_input("Nome do novo domínio *", key=f"hier_dom_novo::{sel_fr}") if criando_dom else ""

    sub_atual = None
    novo_sub_nome = ""
    criando_sub = False
    if dom_atual:
        sub_do_dom = sorted(
            [s for s in subs if int(s["dominio_id"]) == dom_id],
            key=lambda x: (x.get("nome") or "").lower(),
        )
        sub_por_nome = {s["nome"]: s for s in sub_do_dom}
        sel_sub = st.selectbox(
            "Sub-domínio", options=[_HIER_NENHUM] + list(sub_por_nome) + [_HIER_NOVO_SUB],
            key=f"hier_sub::{sel_fr}::{sel_dom}",
        )
        criando_sub = sel_sub == _HIER_NOVO_SUB
        sub_atual = sub_por_nome.get(sel_sub) if sel_sub not in (_HIER_NENHUM, _HIER_NOVO_SUB) else None
        if criando_sub:
            novo_sub_nome = st.text_input(
                "Nome do novo sub-domínio *", key=f"hier_sub_novo::{sel_fr}::{sel_dom}"
            )
    elif criando_dom:
        st.caption("Salve o domínio primeiro; depois selecione-o aqui para adicionar sub-domínios.")

    if sem_fr and not dom_atual:
        st.caption("Selecione um domínio da lista para vinculá-lo a uma franquia ou excluí-lo.")
        return

    # Nível-alvo + modo
    if criando_sub:
        nivel, modo, alvo = "sub", "criar", None
    elif sub_atual:
        nivel, modo, alvo = "sub", "editar", sub_atual
    elif criando_dom:
        nivel, modo, alvo = "dominio", "criar", None
    elif dom_atual:
        nivel, modo, alvo = "dominio", "editar", dom_atual
    elif criando_fr:
        nivel, modo, alvo = "franquia", "criar", None
    else:
        nivel, modo, alvo = "franquia", "editar", fr_atual

    rotulo = {"franquia": "franquia", "dominio": "domínio", "sub": "sub-domínio"}[nivel]
    rk = f"{nivel}_{alvo['id'] if alvo else 'novo'}"

    nome_edit = None
    if modo == "editar":
        nome_edit = st.text_input(f"Nome do {rotulo} *", value=alvo.get("nome") or "", key=f"hier_nome::{rk}")

    # Reatribuir franquia ao editar um domínio (resgata domínios sem franquia
    # e permite mover de uma franquia para outra).
    franquia_destino_id = None
    if modo == "editar" and nivel == "dominio":
        if not fr_por_nome:
            st.warning("Crie uma franquia primeiro para poder vincular este domínio.")
        else:
            fr_nomes = sorted(fr_por_nome)
            atual_fr = next(
                (n for n, f in fr_por_nome.items() if int(f["id"]) == (_hier_franquia_id(alvo) or -1)),
                None,
            )
            idx = fr_nomes.index(atual_fr) if atual_fr in fr_nomes else 0
            escolha = st.selectbox("Franquia *", options=fr_nomes, index=idx, key=f"hier_fr_reassign::{rk}")
            franquia_destino_id = int(fr_por_nome[escolha]["id"])

    desc_in = st.text_input(
        "Descrição", value=(alvo.get("descricao") or "") if alvo else "", key=f"hier_desc::{rk}",
    )

    contexto = {
        "franquia": "nível de topo.",
        "dominio": f"dentro da franquia **{novo_fr_nome or sel_fr}**.",
        "sub": f"dentro do domínio **{sel_dom}**.",
    }[nivel]
    st.caption(f"→ vai **{modo} {rotulo}** — {contexto}")

    c1, c2 = st.columns([1, 1])
    salvar = c1.button("💾 Salvar", type="primary", key=f"hier_save::{rk}")
    excluir = modo == "editar" and c2.button(f"🗑️ Excluir {rotulo}", key=f"hier_del::{rk}")

    desc = desc_in or ""
    if salvar and nivel == "franquia" and modo == "criar":
        nome = (novo_fr_nome or "").strip()
        if not nome:
            st.warning("Informe o nome da nova franquia."); return
        if _count(f"SELECT count(*) FROM {_cad('franquias')} WHERE lower(nome) = {q_str(nome.lower())}"):
            st.error("Já existe uma franquia com esse nome."); return
        run_exec(
            f"INSERT INTO {_cad('franquias')} (nome, descricao, criado_em, criado_por) "
            f"SELECT {q_str(nome)}, {q_str(desc)}, current_timestamp(), {q_str(user)} "
            f"FROM (SELECT 1) WHERE NOT EXISTS "
            f"(SELECT 1 FROM {_cad('franquias')} WHERE lower(nome) = {q_str(nome.lower())})"
        )
        _finish_write(f"Franquia “{nome}” criada.")

    elif salvar and nivel == "dominio" and modo == "criar":
        destino_fr = fr_id
        if criando_fr:
            fnome = (novo_fr_nome or "").strip()
            if not fnome:
                st.warning("Informe o nome da nova franquia."); return
            run_exec(
                f"INSERT INTO {_cad('franquias')} (nome, descricao, criado_em, criado_por) "
                f"SELECT {q_str(fnome)}, '', current_timestamp(), {q_str(user)} "
                f"FROM (SELECT 1) WHERE NOT EXISTS "
                f"(SELECT 1 FROM {_cad('franquias')} WHERE lower(nome) = {q_str(fnome.lower())})"
            )
            destino_fr = _count(
                f"SELECT id FROM {_cad('franquias')} WHERE lower(nome) = {q_str(fnome.lower())} ORDER BY id LIMIT 1"
            )
        if not destino_fr:
            st.error("Não foi possível resolver a franquia."); return
        nome = (novo_dom_nome or "").strip()
        if not nome:
            st.warning("Informe o nome do novo domínio."); return
        if _count(f"SELECT count(*) FROM {_cad('dominios')} WHERE lower(nome) = {q_str(nome.lower())}"):
            st.error("Já existe um domínio com esse nome."); return
        run_exec(
            f"INSERT INTO {_cad('dominios')} (franquia_id, nome, descricao, criado_em, criado_por) "
            f"SELECT {int(destino_fr)}, {q_str(nome)}, {q_str(desc)}, current_timestamp(), {q_str(user)} "
            f"FROM (SELECT 1) WHERE NOT EXISTS "
            f"(SELECT 1 FROM {_cad('dominios')} WHERE lower(nome) = {q_str(nome.lower())})"
        )
        _finish_write(f"Domínio “{nome}” criado.")

    elif salvar and nivel == "sub" and modo == "criar":
        nome = (novo_sub_nome or "").strip()
        if not nome:
            st.warning("Informe o nome do novo sub-domínio."); return
        if _count(
            f"SELECT count(*) FROM {_cad('subdominios')} WHERE dominio_id = {int(dom_id)} "
            f"AND lower(nome) = {q_str(nome.lower())}"
        ):
            st.error("Já existe um sub-domínio com esse nome neste domínio."); return
        run_exec(
            f"INSERT INTO {_cad('subdominios')} (dominio_id, nome, descricao, criado_em, criado_por) "
            f"SELECT {int(dom_id)}, {q_str(nome)}, {q_str(desc)}, current_timestamp(), {q_str(user)} "
            f"FROM (SELECT 1) WHERE NOT EXISTS "
            f"(SELECT 1 FROM {_cad('subdominios')} WHERE dominio_id = {int(dom_id)} "
            f"AND lower(nome) = {q_str(nome.lower())})"
        )
        _finish_write(f"Sub-domínio “{nome}” criado.")

    elif salvar and modo == "editar":
        nome = (nome_edit or "").strip()
        if not nome:
            st.warning(f"Informe o nome do {rotulo}."); return
        tabela = {"franquia": "franquias", "dominio": "dominios", "sub": "subdominios"}[nivel]
        if nivel == "sub":
            dupe = _count(
                f"SELECT count(*) FROM {_cad('subdominios')} WHERE dominio_id = {int(alvo['dominio_id'])} "
                f"AND lower(nome) = {q_str(nome.lower())} AND id <> {int(alvo['id'])}"
            )
        else:
            dupe = _count(
                f"SELECT count(*) FROM {_cad(tabela)} WHERE lower(nome) = {q_str(nome.lower())} "
                f"AND id <> {int(alvo['id'])}"
            )
        if dupe:
            st.error(f"Já existe outr{'a' if nivel == 'franquia' else 'o'} {rotulo} com esse nome."); return
        extra_set = ""
        if nivel == "dominio":
            if not franquia_destino_id:
                st.warning("Escolha a franquia deste domínio."); return
            extra_set = f"franquia_id = {int(franquia_destino_id)}, "
        run_exec(
            f"UPDATE {_cad(tabela)} SET {extra_set}nome = {q_str(nome)}, descricao = {q_str(desc)}, "
            f"atualizado_em = current_timestamp(), atualizado_por = {q_str(user)} "
            f"WHERE id = {int(alvo['id'])}"
        )
        _finish_write(f"{rotulo.capitalize()} atualizado.")

    elif excluir:
        if nivel == "franquia":
            if _count(f"SELECT count(*) FROM {_cad('dominios')} WHERE franquia_id = {int(alvo['id'])}"):
                st.error("Não dá para excluir: há domínios nessa franquia."); return
            run_exec(f"DELETE FROM {_cad('franquias')} WHERE id = {int(alvo['id'])}")
        elif nivel == "dominio":
            dep = (
                _count(f"SELECT count(*) FROM {_cad('subdominios')} WHERE dominio_id = {int(alvo['id'])}")
                + _count(f"SELECT count(*) FROM {_cad('data_stewards')} WHERE dominio_id = {int(alvo['id'])}")
            )
            if dep:
                st.error("Não dá para excluir: há sub-domínios ou data stewards vinculados."); return
            run_exec(f"DELETE FROM {_cad('dominios')} WHERE id = {int(alvo['id'])}")
        else:
            if _count(f"SELECT count(*) FROM {_cad('data_stewards')} WHERE subdominio_id = {int(alvo['id'])}"):
                st.error("Não dá para excluir: há data stewards vinculados."); return
            run_exec(f"DELETE FROM {_cad('subdominios')} WHERE id = {int(alvo['id'])}")
        _finish_write(f"{rotulo.capitalize()} excluído.")


def page_stewards() -> None:
    st.title("🧑‍💼 Data Owners & Stewards")
    st.caption(
        "Cadastro de responsáveis pela árvore **Franquia › Domínio › Sub-domínio**. "
        "O vínculo mínimo é o **domínio**; o sub-domínio é opcional (deixe em "
        "“todo o domínio” para um responsável do domínio inteiro). Escolha logo "
        "abaixo se é um **Data Owner** ou um **Data Steward** — mesmo cadastro."
    )
    _show_cad_feedback()
    role = st.session_state.get("role", "leitor")
    actor = st.session_state.get("user", "")

    frs = list_franquias().to_dict("records")
    fr_nome = {int(f["id"]): f["nome"] for f in frs}
    doms = list_dominios().to_dict("records")
    subs = list_subdominios().to_dict("records")
    dom_nome = {d["id"]: d["nome"] for d in doms}
    dom_fr = {d["id"]: _hier_franquia_id(d) for d in doms}
    sub_nome = {s["id"]: s["nome"] for s in subs}
    _fr_de_dom = lambda i: fr_nome.get(dom_fr.get(i) or -1, "—")
    stw = list_stewards()
    show = stw.copy()
    if not show.empty:
        show["Franquia"] = show["dominio_id"].map(_fr_de_dom)
        show["Domínio"] = show["dominio_id"].map(lambda i: dom_nome.get(i, i))
        show["Sub-domínio"] = show["subdominio_id"].map(
            lambda i: "(todo o domínio)" if pd.isna(i) else sub_nome.get(i, i)
        )
    st.dataframe(
        (show.rename(columns={"tipo": "Tipo", "nome": "Nome", "email": "E-mail"})
             [["Tipo", "Nome", "E-mail", "Franquia", "Domínio", "Sub-domínio"]] if not show.empty else show),
        use_container_width=True, hide_index=True,
    )

    if not can_edit(role):
        st.info("Seu perfil é **leitor** — visualização apenas.")
        return
    if not doms:
        st.warning("Cadastre um **Domínio** primeiro (menu Cadastros → Domínios).")
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
    dom_id = st.selectbox(
        "Domínio *", options=dom_ids,
        format_func=lambda i: f'{_fr_de_dom(i)} › {dom_nome.get(i, i)}', key="stw_dom",
    )
    sub_ids = [s["id"] for s in subs if s["dominio_id"] == dom_id]
    sub_id = st.selectbox(
        "Sub-domínio", options=[None] + sub_ids,
        format_func=lambda i: "— todo o domínio" if i is None else sub_nome.get(i, i),
        key="stw_sub",
    )
    st.caption(
        "Deixe em **“todo o domínio”** para um responsável do domínio inteiro "
        "(típico de **Data Owner**); escolha um sub-domínio para um recorte mais fino."
    )

    sub_sql = "NULL" if sub_id is None else str(int(sub_id))
    sub_match = "subdominio_id IS NULL" if sub_id is None else f"subdominio_id = {int(sub_id)}"

    if st.button(f"💾 Adicionar {tipo_label}", type="primary"):
        if not (nome and email):
            st.warning("Selecione/informe o usuário (nome e e-mail).")
            return
        if "@" not in email or " " in email:
            st.warning("Informe um e-mail corporativo válido.")
            return
        if _count(
            f"SELECT count(*) FROM {_cad('data_stewards')} WHERE tipo = {q_str(tipo)} "
            f"AND dominio_id = {int(dom_id)} "
            f"AND {sub_match} AND lower(email) = {q_str(email.lower())}"
        ):
            st.error(f"Esse {tipo_label} já está vinculado a este domínio/sub-domínio.")
            return
        # INSERT atômico: bloqueia o mesmo e-mail (+ tipo) no mesmo domínio/sub-domínio.
        run_exec(
            f"INSERT INTO {_cad('data_stewards')} (tipo, dominio_id, subdominio_id, nome, email, criado_em, criado_por) "
            f"SELECT {q_str(tipo)}, {int(dom_id)}, {sub_sql}, {q_str(nome)}, {q_str(email)}, current_timestamp(), {q_str(actor)} "
            f"FROM (SELECT 1) WHERE NOT EXISTS "
            f"(SELECT 1 FROM {_cad('data_stewards')} WHERE tipo = {q_str(tipo)} "
            f"AND dominio_id = {int(dom_id)} "
            f"AND {sub_match} AND lower(email) = {q_str(email.lower())})"
        )
        _finish_write(f"{tipo} adicionado.")

    # Excluir
    recs = stw.to_dict("records")
    if recs:
        st.divider()
        st.markdown("#### Excluir")
        opts = [
            f'[{r["tipo"]}] {r["nome"]} <{r["email"]}> — {_fr_de_dom(r["dominio_id"])} › '
            f'{dom_nome.get(r["dominio_id"], r["dominio_id"])} › '
            f'{"(todo o domínio)" if pd.isna(r["subdominio_id"]) else sub_nome.get(r["subdominio_id"], r["subdominio_id"])}'
            f' (id {r["id"]})'
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
        "Cadastro de dashboards AI/BI (Lakeview) publicados. Cada um pertence à "
        "árvore **Franquia › Domínio** (sub-domínio opcional) — quem enxerga o link "
        "no menu Governança é quem for **admin** ou **Data Steward/Owner** daquele "
        "domínio/sub-domínio."
    )
    _show_cad_feedback()
    role = st.session_state.get("role", "leitor")
    user = st.session_state.get("user", "")

    frs = list_franquias().to_dict("records")
    fr_nome = {int(f["id"]): f["nome"] for f in frs}
    doms = list_dominios().to_dict("records")
    subs = list_subdominios().to_dict("records")
    dom_nome = {d["id"]: d["nome"] for d in doms}
    dom_fr = {d["id"]: _hier_franquia_id(d) for d in doms}
    sub_nome = {s["id"]: s["nome"] for s in subs}
    _fr_de_dom = lambda i: fr_nome.get(dom_fr.get(i) or -1, "—")
    dash = list_dashboards()
    show = dash.copy()
    if not show.empty:
        show["Franquia"] = show["dominio_id"].map(_fr_de_dom)
        show["Domínio"] = show["dominio_id"].map(lambda i: dom_nome.get(i, i))
        show["Sub-domínio"] = show["subdominio_id"].map(
            lambda i: sub_nome.get(i, "(todos)") if pd.notna(i) else "(todos)"
        )
    st.dataframe(
        (show.rename(columns={
            "nome": "Nome", "descricao": "Descrição", "url": "URL",
            "icone": "Ícone", "ativo": "Ativo",
        })[["Nome", "Franquia", "Domínio", "Sub-domínio", "URL", "Ícone", "Ativo", "Descrição"]]
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
            "Domínio *", options=dom_ids, index=dom_idx,
            format_func=lambda i: f'{_fr_de_dom(i)} › {dom_nome.get(i, i)}',
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
        and (sub_id is None or p["subdominio_id"] == sub_id or pd.isna(p.get("subdominio_id")))
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
            "**Usuários**."
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


# Rótulos amigáveis de `status_publicacao`, usados tanto na tela de negócio
# (Indicador) quanto na de engenharia (Indicadores — Engenharia).
_STATUS_PUBLICACAO_LABELS = {
    "rascunho": "Rascunho (com o negócio)",
    "aguardando_engenharia": "Aguardando engenharia",
    "pronto_para_ia": "Com a engenharia — pronto para traduzir",
    "validado": "Com a engenharia — validado",
    "publicado": "Publicado",
}


def _render_handoff_engenharia(cur: dict, rk: str, user: str) -> None:
    """Ponte de responsabilidade entre negócio e engenharia, na tela
    Indicador (negócio): depois que o cadastro está completo, o negócio
    clica aqui pra colocar o indicador na fila da tela Indicadores —
    Engenharia, que escolhe as tabelas/colunas e conduz a publicação como
    Metric View (`_render_pipeline_publicacao`). Só é chamada para
    indicadores já salvos (precisa de `cur['id']`)."""
    st.divider()
    st.markdown("#### 🚀 Envio para a Engenharia")
    status = cur.get("status_publicacao") or "rascunho"
    if status == "rascunho":
        st.caption(
            "Quando o cadastro acima estiver completo, envie para a "
            "Engenharia — ela escolhe as tabelas/colunas que formam o "
            "indicador e publica como Metric View."
        )
        if st.button("📤 Enviar para engenharia", key=f"ind_enviar_eng_{rk}", type="primary"):
            run_exec(
                f"UPDATE {_cad('indicadores')} SET status_publicacao = 'aguardando_engenharia', "
                f"atualizado_em = current_timestamp(), atualizado_por = {q_str(user)} "
                f"WHERE id = {int(cur['id'])}"
            )
            _finish_write("Indicador enviado para a Engenharia.")
    else:
        st.success(f"Status: **{_STATUS_PUBLICACAO_LABELS.get(status, status)}**")
        if status == "publicado" and cur.get("metric_view_publicada"):
            st.caption(f"Metric View: `{cur['metric_view_publicada']}`")


def _status_publicacao_pos_lineage(status_atual: str | None, tem_lineage: bool) -> str:
    """Recalcula `status_publicacao` quando a Engenharia salva a lineage de
    Dimensão/Métrica, sem nunca retroceder um indicador já `validado`/
    `publicado` só por reeditar a lineage — a única exceção é lineage vazia,
    que sempre volta pra `aguardando_engenharia` (o gate é incondicional).
    Nunca retrocede a `rascunho`: só o negócio controla esse estágio (botão
    "Enviar para engenharia" em `_render_handoff_engenharia`). Ver
    `_render_pipeline_publicacao` pro botão explícito de "Refazer tradução",
    que é o único jeito deliberado de voltar atrás depois de validado."""
    if not tem_lineage:
        return "aguardando_engenharia"
    if status_atual in ("validado", "publicado"):
        return status_atual
    return "pronto_para_ia"


def _render_pipeline_publicacao(cur: dict, rk: str, user: str) -> None:
    """UI do pipeline de publicação do indicador (blueprint seção 5.1,
    Passos 2 a 5): traduzir a fórmula com IA, testar contra dado real,
    confirmação humana obrigatória, e só então publicar como Metric View.
    Roda na tela Indicadores — Engenharia; só é chamada para indicadores já
    salvos (precisa de `cur['id']`)."""
    st.divider()
    st.markdown("#### 🚀 Pipeline de publicação (Metric View)")
    status = cur.get("status_publicacao") or "rascunho"
    st.caption(f"Status: **{_STATUS_PUBLICACAO_LABELS.get(status, status)}**")

    if status in ("rascunho", "aguardando_engenharia"):
        st.info(
            "Escolha as tabelas/colunas de Dimensão e Métrica (acima) e salve "
            "— só então o indicador fica elegível pra tradução por IA."
        )
        return

    met_items = _parse_tabelas_json(cur.get("metrica_tabelas"))
    met_cols = [c for it in met_items for c in (it.get("colunas") or [])]
    candidato_key = f"ind_candidato_{rk}"

    if status == "pronto_para_ia":
        c1, c2 = st.columns(2)
        with c1:
            if not LLM_ENABLED:
                st.info("Tradução por IA desabilitada (`LLM_ENABLED=false`).")
            elif st.button("🤖 Traduzir fórmula com IA", key=f"ind_traduzir_{rk}"):
                with st.spinner("Traduzindo a fórmula e testando contra o dado real…"):
                    try:
                        resultado = gerar_expr_sql(cur.get("memoria_calculo") or "", met_cols)
                        fonte = met_items[0]
                        valor = testar_candidato(
                            resultado["expr_sql"], fonte["catalogo"], fonte["schema"], fonte["tabela"],
                        )
                        st.session_state[candidato_key] = {**resultado, "valor_teste": valor, "testado": True}
                    except Exception as exc:
                        st.error(f"Falha ao traduzir/testar a fórmula: {exc}")
        with c2:
            # Caminho sem IA: a Engenharia já sabe a expressão (ou prefere
            # escrever à mão) — abre o mesmo editor/teste/confirmação abaixo,
            # só que partindo de um candidato vazio em vez de uma tradução.
            if st.button("✍️ Criar query sem IA", key=f"ind_manual_{rk}"):
                st.session_state[candidato_key] = {
                    "expr_sql": "", "explicacao": "", "valor_teste": None, "testado": False,
                }

        candidato = st.session_state.get(candidato_key)
        if candidato:
            st.markdown("###### Confirmação humana (obrigatória — a expressão só é salva depois de testada e confirmada aqui)")
            st.write(f"**Fórmula original:** {cur.get('memoria_calculo') or '(vazia)'}")
            if candidato.get("explicacao"):
                st.caption(f"Como a IA entendeu: {candidato['explicacao']}")
            expr_edit = st.text_area(
                "Expressão SQL de agregação (edite a sugestão da IA, ou escreva a "
                "sua — ex.: SUM(`Qtd Vendida`), com o nome da coluna entre crases)",
                value=candidato["expr_sql"], key=f"ind_expr_edit_{rk}",
            )
            valor_fmt = "—" if candidato["valor_teste"] is None else f"{candidato['valor_teste']:,.4f}"
            st.metric("Valor de teste (contra o dado real, agora)", valor_fmt)

            # "Confirmar e validar" só libera se o texto atual da caixa é
            # exatamente o que passou no último teste bem-sucedido — editar
            # depois de testar exige testar de novo. Além de garantir que o
            # valor mostrado corresponde ao que será salvo, isso fecha uma
            # brecha de segurança: `_validar_expr_sql_segura` (bloqueia
            # DROP/DELETE/`;`/comentário SQL etc.) só roda dentro de
            # `testar_candidato` — sem essa trava, dava pra digitar algo e
            # confirmar sem nunca passar pelo filtro.
            pronto_pra_confirmar = candidato.get("testado", False) and candidato["expr_sql"] == expr_edit

            c1, c2 = st.columns(2)
            with c1:
                if st.button("🔁 Testar expressão", key=f"ind_retest_{rk}"):
                    if not (expr_edit or "").strip():
                        st.warning("Escreva uma expressão antes de testar.")
                    else:
                        try:
                            fonte = met_items[0]
                            valor = testar_candidato(expr_edit, fonte["catalogo"], fonte["schema"], fonte["tabela"])
                            st.session_state[candidato_key] = {
                                "expr_sql": expr_edit, "explicacao": candidato["explicacao"],
                                "valor_teste": valor, "testado": True,
                            }
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Expressão inválida: {exc}")
            with c2:
                if not pronto_pra_confirmar:
                    st.caption("Teste a expressão acima (como está agora) antes de confirmar.")
                if st.button(
                    "✅ Confirmar e validar", key=f"ind_confirmar_{rk}", type="primary",
                    disabled=not pronto_pra_confirmar,
                ):
                    run_exec(
                        f"UPDATE {_cad('indicadores')} SET expr_validada = {q_str(expr_edit)}, "
                        f"status_publicacao = 'validado', atualizado_em = current_timestamp(), "
                        f"atualizado_por = {q_str(user)} WHERE id = {int(cur['id'])}"
                    )
                    st.session_state.pop(candidato_key, None)
                    _finish_write("Expressão validada — indicador pronto pra publicar.")

    elif status == "validado":
        st.success(f"Expressão validada: `{cur.get('expr_validada')}`")
        try:
            ddl_sql, view_fqn_sugerido = montar_ddl_metric_view(cur)
        except Exception as exc:
            st.error(f"Não foi possível montar o DDL: {exc}")
            ddl_sql = view_fqn_sugerido = None

        if ddl_sql:
            st.markdown("###### 📋 Query final — copie e crie a Metric View onde preferir")
            st.caption(
                "O app não cria a Metric View — só monta o SQL a partir do "
                "que foi validado acima. Rode num SQL Editor/notebook com "
                "`CREATE` no schema de destino (não precisa ser o Service "
                "Principal do app, nem este workspace)."
            )
            st.code(ddl_sql, language="sql")
            fqn_final = st.text_input(
                "Nome final da view (ajuste se rodou em outro catálogo/schema)",
                value=view_fqn_sugerido, key=f"ind_fqn_{rk}",
            )
            if st.button("✅ Marcar como publicado", key=f"ind_marcar_pub_{rk}", type="primary"):
                run_exec(
                    f"UPDATE {_cad('indicadores')} SET status_publicacao = 'publicado', "
                    f"metric_view_publicada = {q_str(fqn_final)}, "
                    f"atualizado_em = current_timestamp(), atualizado_por = {q_str(user)} "
                    f"WHERE id = {int(cur['id'])}"
                )
                _finish_write(f"Indicador marcado como publicado: `{fqn_final}`.")

        if st.button("↩️ Refazer tradução", key=f"ind_refazer_{rk}"):
            run_exec(
                f"UPDATE {_cad('indicadores')} SET status_publicacao = 'pronto_para_ia', "
                f"expr_validada = NULL WHERE id = {int(cur['id'])}"
            )
            _finish_write("Voltou pra pronto_para_ia — pode traduzir de novo.")

    elif status == "publicado":
        st.success(f"✅ Marcado como publicado: `{cur.get('metric_view_publicada')}`")
        st.caption("Autoinformado pela Engenharia — o app não verifica se a view existe de fato.")
        if st.button("↩️ Refazer tradução (marca de novo no fim)", key=f"ind_refazer_pub_{rk}"):
            run_exec(
                f"UPDATE {_cad('indicadores')} SET status_publicacao = 'pronto_para_ia', "
                f"expr_validada = NULL WHERE id = {int(cur['id'])}"
            )
            _finish_write("Voltou pra pronto_para_ia — pode traduzir de novo.")


def _render_glossario_editor(
    *, is_indicador: bool, ont_table: str, list_fn, titulo: str, icone: str,
) -> None:
    """Corpo compartilhado das duas telas de edição do glossário (Glossário de
    Negócio e Indicador). `is_indicador` liga os campos exclusivos de KPI e
    escolhe a tabela de destino (`ont_table`)."""
    kp = "ind" if is_indicador else "glo"  # prefixo de key dos widgets (por tela)
    st.title(f"{icone} {titulo}")
    st.caption(
        "Cadastro de indicadores (KPIs) pelo negócio: objetivo, responsáveis, "
        "unidade, nível de apuração e a fórmula de cálculo em linguagem de "
        "negócio. Quando o cadastro estiver completo, envie para a "
        "Engenharia — ela escolhe as tabelas/colunas e publica o indicador "
        "como Metric View (tela **Indicadores — Engenharia**)."
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
            cols_show += ["Nível de Apuração", "Status"]
            show["Status"] = show["status_publicacao"].fillna("rascunho").map(
                lambda s: _STATUS_PUBLICACAO_LABELS.get(s, s)
            )
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
        "memoria_calculo": "", "restricoes": "", "dimensoes_negocio": "", "decisao_negocio": "",
        "dimensao_tabelas": "[]", "metrica_tabelas": "[]",
        "status_publicacao": "rascunho",
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

    if is_indicador:
        # Indicador não tem Data Owner/Steward próprios — o dono é sempre o
        # Power Steward selecionado acima.
        data_owner = data_steward = power_steward
    else:
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
        # `macroprocesso` é o nome da coluna; na UI Comgás o campo é rotulado "Franquia".
        macroprocesso = st.text_input("Franquia", value=cur.get("macroprocesso") or "", key=f"{kp}_macro_{rk}")
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
    dimensoes_negocio = decisao_negocio = ""
    if is_indicador:
        decisao_negocio = st.text_area(
            "Decisão apoiada", value=cur.get("decisao_negocio") or "", key=f"term_decisao_{rk}",
            help='Que decisão esse indicador ajuda a tomar? Ex.: "decidir se '
                 'abrimos uma nova frente comercial na região X". Junto com o '
                 "Objetivo, vira a descrição da Metric View publicada.",
        )

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
        dimensoes_negocio = st.text_area(
            "Dimensões", value=cur.get("dimensoes_negocio") or "", key=f"term_dimneg_{rk}",
            help='Por quais recortes esse indicador deve poder ser analisado? '
                 'Ex.: "por região, por mês, por tipo de cliente". A '
                 "Engenharia usa esse texto pra escolher as colunas de "
                 "Dimensão.",
        )
        memoria_calculo = st.text_area(
            "Memória de cálculo (fórmula)",
            value=cur.get("memoria_calculo") or "", key=f"term_memoria_{rk}",
            help="Descreva a conta como você explicaria pra alguém do time — "
                 "ex.: \"Receita total dividida pela quantidade de clientes ativos\". "
                 "A Engenharia usa esse texto pra montar a fórmula técnica.",
        )
        restricoes = st.text_area("Restrições", value=cur.get("restricoes") or "", key=f"term_restr_{rk}")
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
                dimensoes_negocio=dimensoes_negocio, decisao_negocio=decisao_negocio,
            )
            # `status_publicacao` só é tocado aqui na criação (sempre nasce
            # 'rascunho'). Em edição, a tela de negócio nunca escreve nessa
            # coluna — quem avança o status daqui pra frente é o botão
            # "Enviar para engenharia" (_render_handoff_engenharia) e, depois,
            # a tela Indicadores — Engenharia (lineage/pipeline). Isso evita
            # que reeditar um campo de negócio (ex.: objetivo) retroceda um
            # indicador que já está com a Engenharia.
            if not editing:
                values["status_publicacao"] = "rascunho"
        if editing:
            set_clause = ", ".join(f"{col} = {q_str(val)}" for col, val in values.items())
            run_exec(
                f"UPDATE {_cad(ont_table)} SET {set_clause}, "
                f"dominio_id = {dom_sql}, subdominio_id = {sub_sql}, "
                f"atualizado_em = current_timestamp(), atualizado_por = {q_str(user)} "
                f"WHERE id = {int(cur['id'])}"
            )
        else:
            cols = ["dominio_id", "subdominio_id", *values.keys(), "criado_em", "criado_por"]
            vals_sql = [dom_sql, sub_sql, *[q_str(v) for v in values.values()], "current_timestamp()", q_str(user)]
            run_exec(
                f"INSERT INTO {_cad(ont_table)} ({', '.join(cols)}) "
                f"VALUES ({', '.join(vals_sql)})"
            )
        st.session_state.pop(_kw_list_key(kp), None)
        st.session_state.pop(f"{kp}_kw_for", None)
        # Se foi um "novo", zera os campos (keys sufixadas com `_novo`).
        if not editing:
            for suf in ("dom", "sub", "owner_sel", "owner_txt", "steward_sel",
                        "steward_txt", "nome", "macro", "obj", "seg", "priv", "obs"):
                st.session_state.pop(f"{kp}_{suf}_novo", None)
            for base in ("ind_ps", "term_unidade", "term_unidade_custom",
                         "term_nivel", "term_vars", "term_restr", "term_memoria",
                         "term_dimneg", "term_decisao"):
                st.session_state.pop(f"{base}_novo", None)
        _finish_write(f"{'Indicador' if is_indicador else 'Termo de negócio'} salvo.")

    if is_indicador and editing:
        _render_handoff_engenharia(cur, rk, user)

    if editing:
        st.divider()
        st.markdown("#### Excluir")
        if st.button(f"🗑️ Excluir {rotulo_item} '{cur['nome']}'", key=f"{kp}_del"):
            run_exec(f"DELETE FROM {_cad(ont_table)} WHERE id = {int(cur['id'])}")
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


def page_indicadores_engenharia() -> None:
    """Fila de indicadores enviados pelo negócio (tela Indicador): escolha
    das tabelas/colunas que formam Dimensão e Métrica, seguida do pipeline
    de tradução/validação/publicação como Metric View
    (`_render_pipeline_publicacao`). Acesso gated pela flag `engenharia` (ou
    admin) — ver `main()`."""
    st.title("🛠️ Indicadores — Engenharia")
    st.caption(
        "Fila de indicadores cadastrados pelo negócio (tela **Indicador**). "
        "Escolha as tabelas/colunas que formam a Dimensão e a Métrica, "
        "depois conduza o pipeline de tradução da fórmula e publicação como "
        "Metric View."
    )
    _show_cad_feedback()
    user = st.session_state.get("user", "")

    termos = list_indicadores()
    fila = termos[termos["status_publicacao"].fillna("rascunho") != "rascunho"]
    if fila.empty:
        st.info("Nenhum indicador enviado pelo negócio no momento.")
        return

    dom_nome = {d["id"]: d["nome"] for d in list_dominios().to_dict("records")}
    recs = fila.to_dict("records")
    opts = [
        f'{r["nome"]} — {_STATUS_PUBLICACAO_LABELS.get(r["status_publicacao"], r["status_publicacao"])} (id {r["id"]})'
        for r in recs
    ]
    sel = st.selectbox("Indicador", options=opts, key="eng_sel")
    cur = recs[opts.index(sel)]
    rk = str(cur["id"])

    st.divider()
    st.markdown(f"### 📈 {cur['nome']}")
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Domínio**\n\n{dom_nome.get(cur.get('dominio_id'), '—') if pd.notna(cur.get('dominio_id')) else '—'}")
    c2.markdown(f"**Data Owner**\n\n{cur.get('data_owner') or '—'}")
    c3.markdown(f"**Nível de apuração**\n\n{cur.get('nivel_apuracao') or '—'}")
    if cur.get("objetivo"):
        st.markdown(f"**Objetivo:** {cur['objetivo']}")
    if cur.get("decisao_negocio"):
        st.markdown(f"**Decisão apoiada:** {cur['decisao_negocio']}")
    st.markdown(f"**Memória de cálculo (fórmula do negócio):** {cur.get('memoria_calculo') or '—'}")
    if cur.get("dimensoes_negocio"):
        st.caption(f"Dimensões desejadas (descrição do negócio): {cur['dimensoes_negocio']}")
    if cur.get("variaveis_utilizadas"):
        st.caption(f"Variáveis utilizadas: {cur['variaveis_utilizadas']}")
    if cur.get("restricoes"):
        st.caption(f"Restrições: {cur['restricoes']}")

    st.divider()
    _sync_tabela_picker_state("dim", rk, _parse_tabelas_json(cur.get("dimensao_tabelas")))
    _sync_tabela_picker_state("met", rk, _parse_tabelas_json(cur.get("metrica_tabelas")))
    st.markdown("###### Dimensão — tabelas e colunas que compõem a dimensão")
    dim_items = _render_tabela_picker(user, "dim")
    st.markdown("###### Métrica — tabelas e colunas que formam a métrica")
    met_items = _render_tabela_picker(user, "met")

    if st.button("💾 Salvar construção do indicador", type="primary", key=f"eng_save_{rk}"):
        novo_status = _status_publicacao_pos_lineage(
            cur.get("status_publicacao"), bool(dim_items and met_items),
        )
        run_exec(
            f"UPDATE {_cad('indicadores')} SET "
            f"dimensao_tabelas = {q_str(_dump_tabelas_json(dim_items))}, "
            f"metrica_tabelas = {q_str(_dump_tabelas_json(met_items))}, "
            f"status_publicacao = {q_str(novo_status)}, "
            f"atualizado_em = current_timestamp(), atualizado_por = {q_str(user)} "
            f"WHERE id = {int(cur['id'])}"
        )
        for kind in ("dim", "met"):
            st.session_state.pop(f"term_{kind}_items", None)
            st.session_state.pop(f"term_{kind}_items_for", None)
        _finish_write("Lineage do indicador salvo.")

    _render_pipeline_publicacao(cur, rk, user)


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
    c3.markdown(f"**Franquia**\n\n{cur.get('macroprocesso') or '—'}")

    if cur.get("palavras_chave"):
        st.markdown(f"**Palavras-chave:** {cur['palavras_chave']}")
    if cur.get("objetivo"):
        st.markdown("**Objetivo**" if is_indicador else "**Definição**")
        st.write(cur["objetivo"])
    if is_indicador and cur.get("decisao_negocio"):
        st.markdown("**Decisão apoiada**")
        st.write(cur["decisao_negocio"])

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
        if cur.get("dimensoes_negocio"):
            st.markdown("**Dimensões**")
            st.write(cur["dimensoes_negocio"])
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


# ---------------------------------------------------------------------------
# Cadastro de Acesso a Dados (blueprint) — SKELETON
# ---------------------------------------------------------------------------
# Duas telas CRUD sobre tabelas que alimentam políticas ABAC do UC. Acesso
# restrito a admin (o blueprint deixa "perfil admin de acesso dedicado" como
# pergunta aberta). Log de antes/depois em `log_cadastros`.


_SGU_NENHUM = "— (nenhum)"
_SGU_MANUAL = "✍️ outro (digitar)"


def _fmt_principal(x: dict) -> str:
    return f'{x["rotulo"]}  ·  {x["ident"]}' if x["rotulo"] != x["ident"] else x["ident"]


def _seletor_grupo_usuario(key_prefix: str) -> tuple[str | None, str | None, str | None]:
    """Componente: escolhe o **usuário** por busca type-ahead no diretório e,
    opcionalmente, o **grupo** (só rótulo).

    Retorna (grupo_nome, grupo_id, usuario_ident). O `usuario_ident` é o que
    entra na tabela — e é o que o UDF ABAC casa com current_user() (userName
    para pessoa, applicationId para service principal).
    """
    grupos = list_grupos()

    # --- grupo: rótulo opcional ---
    g_nome = g_id = None
    if grupos:
        escolha = st.selectbox(
            "Grupo (Entra ID) — rótulo, opcional",
            options=[_SGU_NENHUM] + [g["nome"] for g in grupos] + [_SGU_MANUAL],
            key=f"{key_prefix}_grupo",
        )
        if escolha == _SGU_MANUAL:
            g_nome = st.text_input("Nome do grupo", key=f"{key_prefix}_grupo_txt").strip() or None
        elif escolha != _SGU_NENHUM:
            g = next(x for x in grupos if x["nome"] == escolha)
            g_nome, g_id = g["nome"], g["id"]
    else:
        erro = st.session_state.get("_grupos_erro")
        if erro:
            st.caption(f"⚠️ diagnóstico (grupos): {erro}")
        g_nome = st.text_input(
            "Grupo (Entra ID) — opcional", key=f"{key_prefix}_grupo_txt2"
        ).strip() or None

    # --- usuário: busca type-ahead (escala; não depende de membership) ---
    termo = st.text_input(
        "Usuário — buscar por nome ou login *", key=f"{key_prefix}_busca",
        placeholder="ex.: joao.silva  /  João Silva  /  teste-usuario",
    )
    cands = buscar_principais(termo)
    erro = st.session_state.get("_grupos_erro")
    if termo and erro:
        st.caption(f"⚠️ diagnóstico (busca): {erro}")

    if cands:
        m = st.selectbox(
            "Resultado *", options=cands, format_func=_fmt_principal,
            key=f"{key_prefix}_membro",
        )
        return (g_nome, g_id, m["ident"])

    if termo and len(termo.strip()) >= 2:
        st.caption(
            "Ninguém encontrado na busca — informe o identificador exato "
            "(o que o UDF casa com `current_user()`)."
        )
    u = st.text_input(
        "Usuário (identificador exato) *", key=f"{key_prefix}_u_manual"
    ).strip()
    return (g_nome, g_id, u or None)


def page_mapa_dominio_acesso() -> None:
    st.title("🗺️ Acesso por Franquia")
    st.caption(
        "Quais **domínios** (dentro de uma franquia) cada usuário pode ver. "
        "Alimenta a política ABAC (row filter) do Unity Catalog. Um usuário pode "
        "ter várias linhas (cross-domínio = ter mais de uma linha, nunca uma "
        "flag). A concessão é no nível do **domínio**; o **sub-domínio** é "
        "opcional e restringe ainda mais. **SKELETON** — em validação."
    )
    _show_cad_feedback()
    actor = st.session_state.get("user", "")

    frs = list_franquias().to_dict("records")
    fr_nome = {f["id"]: f["nome"] for f in frs}
    doms = list_dominios().to_dict("records")
    subs = list_subdominios().to_dict("records")
    dom_nome = {d["id"]: d["nome"] for d in doms}
    dom_fr = {d["id"]: d.get("franquia_id") for d in doms}
    sub_nome = {s["id"]: s["nome"] for s in subs}

    df = list_mapa_dominio_acesso()
    show = df.copy()
    if not show.empty:
        show["Franquia"] = show["dominio_id"].map(lambda i: fr_nome.get(dom_fr.get(i), "—"))
        show["Domínio"] = show["dominio_id"].map(lambda i: dom_nome.get(i, i))
        show["Sub-domínio"] = show["subdominio_id"].map(
            lambda i: sub_nome.get(i, "(todo o domínio)") if pd.notna(i) else "(todo o domínio)"
        )
    st.dataframe(
        (show.rename(columns={"grupo": "Grupo", "usuario": "Usuário"})
             [["Grupo", "Usuário", "Franquia", "Domínio", "Sub-domínio", "criado_por", "criado_em"]]
         if not show.empty else show),
        use_container_width=True, hide_index=True,
    )

    if not frs or not doms:
        st.warning("Cadastre uma **Franquia** e um **Domínio** primeiro (menu Cadastros → Domínios).")
        return

    st.divider()
    st.markdown("#### Adicionar acesso")
    g_nome, g_id, usuario = _seletor_grupo_usuario("mda")

    fr_ids = [f["id"] for f in frs]
    fr_id = st.selectbox("Franquia *", options=fr_ids,
                         format_func=lambda i: fr_nome.get(i, i), key="mda_fr")
    dom_ids = [d["id"] for d in doms if dom_fr.get(d["id"]) == fr_id]
    if not dom_ids:
        st.info("Essa franquia ainda não tem domínios cadastrados.")
        return
    dom_id = st.selectbox("Domínio *", options=dom_ids,
                          format_func=lambda i: dom_nome.get(i, i), key="mda_dom")
    sub_ids = [s["id"] for s in subs if s["dominio_id"] == dom_id]
    sub_id = st.selectbox(
        "Sub-domínio (opcional — vazio = todo o domínio)",
        options=[None] + sub_ids,
        format_func=lambda i: "(todo o domínio)" if i is None else sub_nome.get(i, i),
        key="mda_sub",
    )

    if st.button("💾 Adicionar", type="primary"):
        if not usuario:
            st.warning("Selecione/informe o usuário.")
            return
        dup = _count(
            f"SELECT count(*) FROM {_cad('mapa_dominio_acesso')} "
            f"WHERE lower(usuario) = {q_str(usuario.lower())} AND dominio_id = {int(dom_id)} "
            f"AND {'subdominio_id IS NULL' if sub_id is None else f'subdominio_id = {int(sub_id)}'}"
        )
        if dup:
            st.error("Esse usuário já tem esse acesso (mesmo domínio/sub-domínio).")
            return
        novo_id = str(uuid.uuid4())
        depois = {
            "grupo": g_nome, "usuario": usuario, "dominio": dom_nome.get(dom_id),
            "subdominio": None if sub_id is None else sub_nome.get(sub_id),
        }
        run_exec(
            f"INSERT INTO {_cad('mapa_dominio_acesso')} "
            "(id, grupo, grupo_id, usuario, dominio_id, subdominio_id, dominio, subdominio, "
            "criado_em, criado_por) VALUES ("
            f"{q_str(novo_id)}, {_qn(g_nome)}, {_qn(g_id)}, {q_str(usuario)}, "
            f"{int(dom_id)}, {'NULL' if sub_id is None else int(sub_id)}, "
            f"{_qn(dom_nome.get(dom_id))}, "
            f"{_qn(None if sub_id is None else sub_nome.get(sub_id))}, "
            f"current_timestamp(), {q_str(actor)})"
        )
        _log_cadastro(actor, "mapa_dominio_acesso", "INSERT", novo_id, None, depois)
        _finish_write("Acesso por domínio adicionado.")

    recs = df.to_dict("records")
    if recs:
        st.divider()
        st.markdown("#### Excluir")
        opts = [
            f'{r["usuario"]} → {dom_nome.get(r["dominio_id"], r["dominio_id"])}'
            f'{"" if pd.isna(r["subdominio_id"]) else " › " + str(sub_nome.get(r["subdominio_id"], r["subdominio_id"]))}'
            f'  (id {r["id"][:8]})'
            for r in recs
        ]
        sel = st.selectbox("Registro", options=opts, key="mda_del")
        if st.button("🗑️ Excluir registro selecionado"):
            r = recs[opts.index(sel)]
            run_exec(f"DELETE FROM {_cad('mapa_dominio_acesso')} WHERE id = {q_str(r['id'])}")
            _log_cadastro(actor, "mapa_dominio_acesso", "DELETE", r["id"],
                          {"usuario": r["usuario"], "dominio": r.get("dominio")}, None)
            _finish_write("Registro excluído.")


def page_mapa_sensibilidade_acesso() -> None:
    st.title("🔐 Acesso por Sensibilidade")
    st.caption(
        "Nível de confidencialidade e categorias de dado pessoal que cada "
        "usuário pode ver, **independente de domínio**. Exatamente **uma linha "
        "por usuário** — se já existir, o formulário abre em modo edição. "
        "**SKELETON** — em validação."
    )
    _show_cad_feedback()
    actor = st.session_state.get("user", "")

    df = list_mapa_sensibilidade_acesso()
    st.dataframe(
        (df.rename(columns={
            "grupo": "Grupo", "usuario": "Usuário",
            "nivel_max_confidencialidade": "Nível máx.",
            "pode_ver_dado_pessoal": "Dado pessoal",
            "pode_ver_dado_pessoal_sensivel": "Dado pessoal sensível",
        })[["Grupo", "Usuário", "Nível máx.", "Dado pessoal", "Dado pessoal sensível",
            "criado_por", "atualizado_em"]] if not df.empty else df),
        use_container_width=True, hide_index=True,
    )

    st.divider()
    st.markdown("#### Adicionar / editar")
    g_nome, g_id, usuario = _seletor_grupo_usuario("msa")

    atual = None
    if usuario and not df.empty:
        m = df[df["usuario"].str.lower() == usuario.lower()]
        if not m.empty:
            atual = m.iloc[0].to_dict()
            st.info(f"**{usuario}** já tem acesso cadastrado — salvando você **edita** a linha existente.")

    nivel = st.selectbox(
        "Nível máx. de confidencialidade *", options=_NIVEIS_CONFIDENCIALIDADE,
        index=_NIVEIS_CONFIDENCIALIDADE.index(atual["nivel_max_confidencialidade"])
        if atual and atual.get("nivel_max_confidencialidade") in _NIVEIS_CONFIDENCIALIDADE else 1,
        key="msa_nivel",
    )
    c1, c2 = st.columns(2)
    with c1:
        pdp = st.checkbox("Pode ver dado pessoal",
                          value=bool(atual["pode_ver_dado_pessoal"]) if atual else False, key="msa_pdp")
    with c2:
        pdps = st.checkbox("Pode ver dado pessoal sensível",
                           value=bool(atual["pode_ver_dado_pessoal_sensivel"]) if atual else False, key="msa_pdps")

    if st.button("💾 Salvar", type="primary"):
        if not usuario:
            st.warning("Selecione/informe o usuário.")
            return
        depois = {"grupo": g_nome, "usuario": usuario, "nivel": nivel,
                  "dado_pessoal": pdp, "dado_pessoal_sensivel": pdps}
        if atual:
            run_exec(
                f"UPDATE {_cad('mapa_sensibilidade_acesso')} SET "
                f"grupo = {_qn(g_nome)}, grupo_id = {_qn(g_id)}, "
                f"nivel_max_confidencialidade = {q_str(nivel)}, "
                f"pode_ver_dado_pessoal = {str(pdp).lower()}, "
                f"pode_ver_dado_pessoal_sensivel = {str(pdps).lower()}, "
                f"atualizado_em = current_timestamp(), atualizado_por = {q_str(actor)} "
                f"WHERE id = {q_str(atual['id'])}"
            )
            _log_cadastro(actor, "mapa_sensibilidade_acesso", "UPDATE", atual["id"],
                          {k: atual.get(k) for k in
                           ("nivel_max_confidencialidade", "pode_ver_dado_pessoal",
                            "pode_ver_dado_pessoal_sensivel")}, depois)
            _finish_write("Acesso por sensibilidade atualizado.")
        else:
            novo_id = str(uuid.uuid4())
            run_exec(
                f"INSERT INTO {_cad('mapa_sensibilidade_acesso')} "
                "(id, grupo, grupo_id, usuario, nivel_max_confidencialidade, "
                "pode_ver_dado_pessoal, pode_ver_dado_pessoal_sensivel, criado_em, criado_por) VALUES ("
                f"{q_str(novo_id)}, {_qn(g_nome)}, {_qn(g_id)}, {q_str(usuario)}, "
                f"{q_str(nivel)}, {str(pdp).lower()}, {str(pdps).lower()}, "
                f"current_timestamp(), {q_str(actor)})"
            )
            _log_cadastro(actor, "mapa_sensibilidade_acesso", "INSERT", novo_id, None, depois)
            _finish_write("Acesso por sensibilidade adicionado.")

    recs = df.to_dict("records")
    if recs:
        st.divider()
        st.markdown("#### Excluir (usuário cai no default mais restritivo)")
        opts = [f'{r["usuario"]}  ·  {r["nivel_max_confidencialidade"]}  (id {r["id"][:8]})' for r in recs]
        sel = st.selectbox("Registro", options=opts, key="msa_del")
        if st.button("🗑️ Excluir registro selecionado"):
            r = recs[opts.index(sel)]
            run_exec(f"DELETE FROM {_cad('mapa_sensibilidade_acesso')} WHERE id = {q_str(r['id'])}")
            _log_cadastro(actor, "mapa_sensibilidade_acesso", "DELETE", r["id"],
                          {"usuario": r["usuario"]}, None)
            _finish_write("Registro excluído.")


def page_permissoes() -> None:
    st.title("🔒 Usuários")
    st.caption(
        "Cadastro de usuários (espelho do workspace) e seu permissionamento. "
        "**Papel** define o que edita nos cadastros (admin/editor/leitor). As "
        "**checkboxes** liberam menus por usuário: *Cadastro* libera Domínios "
        "(Franquias / Domínios / Sub-domínios) e Glossário de Negócio; *Governança* libera Governança de "
        "Dados, Auditoria, FinOps e a visualização (sem decidir) do Backlog de "
        "Aprovação de Tags; *Aprovador de tags* libera tudo de Governança **mais** "
        "aprovar/rejeitar no Backlog; *Ver FinOps* libera só a tela FinOps; "
        "*Power Steward* além de aparecer no campo Power Steward da tela Indicador, "
        "libera o menu Cadastros **completo** (Domínios, Data Owners "
        "& Stewards, Dashboards, Padrões de Dado Pessoal, Glossário de Negócio e "
        "Indicador — tudo, menos esta tela); *Engenharia* libera a tela Indicadores "
        "— Engenharia (escolha de tabelas/colunas e publicação como Metric View). "
        "**Admin enxerga/faz tudo** independentemente das checkboxes. Só admins "
        "acessam esta tela."
    )
    _show_cad_feedback()
    user = st.session_state.get("user", "")

    df = list_permissoes()
    st.dataframe(
        df.rename(columns={
            "id": "ID", "nome": "Nome", "email": "E-mail", "papel": "Papel",
            "ver_cadastros": "Cadastro", "ver_logs": "Governança",
            "aprovador_tags": "Aprovador de tags", "ver_finops": "Ver FinOps",
            "power_steward": "Power Steward", "engenharia": "Engenharia",
            "admin_acesso": "Acesso a Dados",
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
    ca, cb, cc, cd, ce, cf, cg = st.columns(7)
    with ca:
        add_ver_cad = st.checkbox("Cadastro", value=True, key="perm_add_ver_cad")
    with cb:
        add_ver_log = st.checkbox("Governança", value=False, key="perm_add_ver_log")
    with cc:
        add_aprov = st.checkbox("Aprovador de tags", value=False, key="perm_add_aprov")
    with cd:
        add_finops = st.checkbox("Ver FinOps", value=False, key="perm_add_finops")
    with ce:
        add_power = st.checkbox("Power Steward", value=False, key="perm_add_power")
    with cf:
        add_eng = st.checkbox("Engenharia", value=False, key="perm_add_eng")
    with cg:
        add_acesso = st.checkbox("Acesso a Dados", value=False, key="perm_add_acesso")
    st.caption("Admin ignora as checkboxes (vê/faz tudo). *Power Steward* também "
               "libera o menu Cadastros completo, além de ser o rótulo que aparece "
               "no campo Power Steward do Indicador. *Acesso a Dados* libera as "
               "telas de Acesso por Franquia e por Sensibilidade.")
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
            f"(nome, email, papel, ver_cadastros, ver_logs, aprovador_tags, ver_finops, power_steward, engenharia, admin_acesso, criado_em, criado_por) "
            f"SELECT {q_str((nome_add or '').strip())}, {q_str(em)}, {q_str(papel_add)}, "
            f"{str(add_ver_cad).lower()}, {str(add_ver_log).lower()}, {str(add_aprov).lower()}, "
            f"{str(add_finops).lower()}, {str(add_power).lower()}, {str(add_eng).lower()}, {str(add_acesso).lower()}, current_timestamp(), {q_str(user)} "
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
        e1, e2, e3, e4, e5, e6, e7 = st.columns(7)
        with e1:
            ed_ver_cad = st.checkbox(
                "Cadastro", value=_as_bool(cur.get("ver_cadastros")), key=f"perm_edit_ver_cad_{rid}")
        with e2:
            ed_ver_log = st.checkbox(
                "Governança", value=_as_bool(cur.get("ver_logs")), key=f"perm_edit_ver_log_{rid}")
        with e3:
            ed_aprov = st.checkbox(
                "Aprovador de tags", value=_as_bool(cur.get("aprovador_tags")), key=f"perm_edit_aprov_{rid}")
        with e4:
            ed_finops = st.checkbox(
                "Ver FinOps", value=_as_bool(cur.get("ver_finops")), key=f"perm_edit_finops_{rid}")
        with e5:
            ed_power = st.checkbox(
                "Power Steward", value=_as_bool(cur.get("power_steward")), key=f"perm_edit_power_{rid}")
        with e6:
            ed_eng = st.checkbox(
                "Engenharia", value=_as_bool(cur.get("engenharia")), key=f"perm_edit_eng_{rid}")
        with e7:
            ed_acesso = st.checkbox(
                "Acesso a Dados", value=_as_bool(cur.get("admin_acesso")), key=f"perm_edit_acesso_{rid}")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Salvar"):
                run_exec(
                    f"UPDATE {_cad('permissoes')} SET nome = {q_str((ed_nome or '').strip())}, "
                    f"papel = {q_str(novo)}, "
                    f"ver_cadastros = {str(ed_ver_cad).lower()}, ver_logs = {str(ed_ver_log).lower()}, "
                    f"aprovador_tags = {str(ed_aprov).lower()}, ver_finops = {str(ed_finops).lower()}, "
                    f"power_steward = {str(ed_power).lower()}, engenharia = {str(ed_eng).lower()}, "
                    f"admin_acesso = {str(ed_acesso).lower()}, "
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
    role = st.session_state.get("role", "leitor")
    perms = st.session_state.get("perms", {}) or {}
    pode_decidir = role == "admin" or bool(perms.get("aprovador_tags"))

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

        if not pode_decidir:
            st.info(
                "Seu acesso aqui é só de visualização — decidir itens exige "
                "a flag **Aprovador de tags** (tela Usuários)."
            )

    if not pend.empty and pode_decidir:
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
            "termos": _count(f"SELECT count(*) FROM {_cad('glossario_negocio')} WHERE {wk}"),
            "indicadores": _count(f"SELECT count(*) FROM {_cad('indicadores')} WHERE {wk}"),
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
    is_power = bool(perms.get("power_steward"))
    # Power Steward libera o menu Cadastros completo E a página Governança de
    # Dados (ver `main()`) — mas NÃO Auditoria (isso segue exigindo
    # `ver_logs`/`aprovador_tags`). `can_cad` conta Power Steward pro bloco
    # "Saúde dos cadastros"; `can_logs` (bloco "Atividade recente") não conta.
    can_cad = is_admin or bool(perms.get("ver_cadastros")) or is_power
    can_logs = is_admin or bool(perms.get("ver_logs")) or can_aprov
    can_eng = is_admin or bool(perms.get("engenharia"))

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
                ("ver_cadastros", "Cadastro"),
                ("ver_logs", "Governança"),
                ("ver_finops", "Ver FinOps"),
                ("engenharia", "Engenharia"),
            ) if perms.get(flag)
        ]
    cls = "inicio-chip leitor" if role == "leitor" else "inicio-chip"
    st.markdown("".join(f'<span class="{cls}">{c}</span>' for c in chips), unsafe_allow_html=True)

    n_pend = len(list_tag_backlog("pendente")) if can_aprov else 0
    lacunas = _lacunas_cadastro(doms, subs, stew, glo, ind) if can_cad else []
    n_lac = sum(x[0] for x in lacunas)
    meus_ind = _meus_indicadores(ind, user) if (is_power or is_admin) else []
    fila_eng = ind[ind["status_publicacao"].fillna("rascunho") != "rascunho"] if can_eng else ind.iloc[0:0]

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
                   "Assistente. Peça a um admin para incluir seu e-mail em **Usuários**.")
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

    # ---- Fila de Engenharia ----
    if can_eng:
        with esq.container(border=True, key="inicio_fila_eng"):
            st.markdown(f"##### 🛠️ Fila de Engenharia  ·  {len(fila_eng)}")
            if fila_eng.empty:
                st.caption("Nenhum indicador aguardando a Engenharia. 🎉")
            else:
                for r in fila_eng.head(5).to_dict("records"):
                    status = _STATUS_PUBLICACAO_LABELS.get(r.get("status_publicacao"), r.get("status_publicacao"))
                    st.markdown(f"**{r['nome']}** — {status}")
            _atalho("indicadores_engenharia", "Abrir Indicadores — Engenharia", "🛠️")

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
        page_title=APP_NAME,
        page_icon="🏷️",
        layout="wide",
        initial_sidebar_state="collapsed",
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
        "aprovador_tags": False, "ver_finops": False, "power_steward": False,
        "engenharia": False, "registrado": False, "nome": "",
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
    pg_consulta = st.Page(page_consulta_termos, title="Termos de Negócio", icon="📚")
    nav_pages["consulta"] = pg_consulta

    pages: dict = {"Painel": [pg_inicio]}

    # Cadastro: a flag `ver_cadastros` (rótulo "Cadastro") libera só Domínios
    # (página única com Franquias / Domínios / Sub-domínios em abas) e
    # Glossário de Negócio. `power_steward` libera o conjunto COMPLETO (as
    # mesmas páginas de admin, exceto Usuários) — quem cuida de indicador de
    # ponta a ponta também precisa de Data Owners/Dashboards/Padrões de Dado
    # Pessoal, não só do glossário.
    cadastro_completo = is_admin or perms["power_steward"]
    cadastro_algum = cadastro_completo or perms["ver_cadastros"]
    if cadastro_algum:
        pg_glossario_edit = st.Page(page_glossario_negocio, title="Glossário de Negócio", icon="📖")
        nav_pages["glossario_edit"] = pg_glossario_edit
        cadastros = [
            st.Page(page_dominios, title="Domínios", icon="🗂️"),
        ]
        if cadastro_completo:
            pg_stewards = st.Page(page_stewards, title="Data Owners & Stewards", icon="🧑‍💼")
            nav_pages["stewards"] = pg_stewards
            cadastros += [
                pg_stewards,
                st.Page(page_dashboards, title="Dashboards", icon="📊"),
                st.Page(page_padroes_dado_pessoal, title="Padrões de Dado Pessoal", icon="🧬"),
            ]
        cadastros.append(pg_glossario_edit)
        if cadastro_completo:
            pg_indicadores = st.Page(page_indicadores, title="Indicador", icon="📈")
            nav_pages["indicadores"] = pg_indicadores
            cadastros.append(pg_indicadores)
        pages["Cadastros"] = cadastros

    # Usuários (antiga "Usuários & Permissões") — sempre admin-only, agora num
    # menu próprio em vez de dentro de Cadastros (nem `ver_cadastros` nem
    # `power_steward` dão acesso a ela).
    if is_admin:
        pages["Admin"] = [st.Page(page_permissoes, title="Usuários", icon="🔒")]

    # Cadastro de Acesso a Dados (blueprint) — liberado pela flag `admin_acesso`
    # (mesma regra dos outros menus: ter a flag = ver e usar). Admin ignora.
    if is_admin or perms["admin_acesso"]:
        pages["Acesso a Dados"] = [
            st.Page(page_mapa_dominio_acesso, title="Acesso por Franquia", icon="🗺️"),
            st.Page(page_mapa_sensibilidade_acesso, title="Acesso por Sensibilidade", icon="🔐"),
        ]

    # Menu Governança (só a página Governança de Dados + dashboards):
    # liberado por `ver_logs` (rótulo "Governança"), `aprovador_tags` ou
    # `power_steward`. Auditoria, Aprovações (Backlog) e FinOps são menus
    # PRÓPRIOS, à parte — `power_steward` não dá acesso a eles (quando essa
    # flag está marcada, ela só libera Cadastros completo + Governança +
    # Glossário, nada mais); só `ver_logs`/`aprovador_tags`/`ver_finops`
    # abrem esses três.
    pode_governanca = is_admin or perms["ver_logs"] or perms["aprovador_tags"] or perms["power_steward"]
    if pode_governanca:
        pg_governanca = st.Page(page_governanca, title="Governança de Dados", icon="🏷️")
        nav_pages["governanca"] = pg_governanca
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
        pages["Governança"] = governanca
    # Auditoria e Aprovações (Backlog) exigem `ver_logs`/`aprovador_tags` — ao
    # contrário da página Governança de Dados, `power_steward` sozinho NÃO
    # dá acesso aqui.
    pode_auditoria = is_admin or perms["ver_logs"] or perms["aprovador_tags"]
    if pode_auditoria:
        pg_relatorio = st.Page(page_relatorio_auditoria, title="Relatório de Auditoria", icon="📋")
        nav_pages["auditoria"] = pg_relatorio
        pages["Auditoria"] = [
            pg_relatorio,
            st.Page(page_log_comentarios, title="Log de comentários", icon="📜"),
            st.Page(page_log_tags, title="Log de tags", icon="🏷️"),
        ]
        pg_backlog = st.Page(page_tag_backlog, title="Backlog de Aprovação de Tags", icon="✅")
        nav_pages["backlog"] = pg_backlog
        pages["Aprovações"] = [pg_backlog]
    pages["Glossário"] = [pg_consulta]
    # O controle de acesso *por domínio* ao FinOps (blueprint seção 8.1,
    # Passo 5: um steward de um Spoke só veria o custo do seu domínio) ainda
    # não existe no modelo de permissões do app (`permissoes` é papel global
    # + flags de página, sem escopo por domínio) e não foi criado aqui pra
    # não inventar um mecanismo de ACL inteiro fora do pedido.
    # `obter_custo_por_dominio` já devolve os dados com a coluna `dominio`
    # pronta pra filtrar quando esse controle existir. `power_steward` NÃO
    # entra aqui — FinOps é dado financeiro, fora do escopo do que Power
    # Steward enxerga por padrão.
    pode_finops = is_admin or perms["ver_logs"] or perms["aprovador_tags"] or perms["ver_finops"]
    if pode_finops:
        pg_finops = st.Page(page_finops, title="FinOps", icon="💰")
        nav_pages["finops"] = pg_finops
        pages["FinOps"] = [pg_finops]
    # Engenharia: fila de indicadores enviados pelo negócio (escolha de
    # tabelas/colunas + pipeline de publicação como Metric View). Flag
    # própria (`engenharia`) — separada de `ver_cadastros` porque é um papel
    # de trabalho diferente (quem mexe em lineage técnico), não quem cadastra
    # domínios/glossário.
    if is_admin or perms["engenharia"]:
        pg_indicadores_eng = st.Page(page_indicadores_engenharia, title="Indicadores — Engenharia", icon="🛠️")
        nav_pages["indicadores_engenharia"] = pg_indicadores_eng
        pages["Engenharia"] = [pg_indicadores_eng]
    st.session_state["_nav_pages"] = nav_pages
    nav = st.navigation(pages)

    nav.run()

    # Assistente de IA: painel ancorado à direita (segunda "sidebar"), que
    # recolhe para uma aba fina no canto direito. Streamlit só tem uma sidebar
    # nativa (esquerda), então o painel é um container fixo via CSS. Opcional
    # (LLM_ENABLED) — sem isso, nada é renderizado.
    if LLM_ENABLED:
        render_assistant_dock(user)


if __name__ == "__main__":
    main()
