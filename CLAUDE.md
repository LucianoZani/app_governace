# Contexto para o Claude Code — `governanca-unity-catalog`

> **Este é o repositório real e publicado do app.** Existe uma **cópia
> desatualizada** em `C:\Users\Luciano.Zani\Documents\Appdatabricks\governanca-unity-catalog\`
> (app.py ~1815 linhas) — **nunca** trabalhe/publique a partir dela.
> Remote: `github.com/LucianoZani/app_governace` (público), branch `main`.

Usuário: Luciano Zani. Responder e documentar em **português**.

---

## O que é

Databricks App (Streamlit) de governança no Unity Catalog. Quatro áreas:
Governança de Dados (tags/comentários), Cadastros & Administração, Assistente
de IA (opcional, `LLM_ENABLED`) e Glossário. Detalhes em `docs-produto/` (guia
de produto, multi-cliente) e `docs/` (specifics do ambiente pessoal atual).

Arquitetura recorrente: `databricks-sdk` + Statement Execution API contra um
SQL Warehouse. **Identidade:** OBO (token do usuário logado) para leituras/
navegação; **service principal** do app para escritas privilegiadas e dados
internos. Hoje o ambiente pessoal roda com `USE_ON_BEHALF_OF_USER=false` (tudo
como SP). Antes de mudar a invariante de identidade, releia `docs-produto/02`.

Cadastros internos: catálogo `apps`, schema `governanca_unity_catalog_<env>`.
Glossário: schema `ontologia_<env>` (fixo em código, `ONTOLOGIA_SCHEMA`).

## Ambiente pessoal atual (Databricks Free Edition)

- Workspace: `https://dbc-7b532bee-e109.cloud.databricks.com` · perfil CLI `governanca-free`
- App: `governanca-unity-catalog` · URL `https://governanca-unity-catalog-7474649363876533.aws.databricksapps.com`
- Source do app no Workspace: `/Workspace/Users/lucianozaniengenheirodedados@gmail.com/apps/governanca-unity-catalog`
- SQL Warehouse: `20dfe5c08c3fa359` · `ENVIRONMENT=prd` (sem dev/prd separado nesta conta)

## Deploy (manual, fora de CI)

```bash
cd <este repo>
MSYS_NO_PATHCONV=1 databricks sync . "/Workspace/Users/lucianozaniengenheirodedados@gmail.com/apps/governanca-unity-catalog" --full -p governanca-free
MSYS_NO_PATHCONV=1 databricks apps deploy governanca-unity-catalog --source-code-path "/Workspace/Users/lucianozaniengenheirodedados@gmail.com/apps/governanca-unity-catalog" -p governanca-free
```

- `MSYS_NO_PATHCONV=1` é obrigatório no Git Bash (senão `/Workspace/...` vira `C:/Program Files/Git/Workspace/...`).
- Só `app.py`, `app.yaml`, `requirements.txt` afetam o runtime (`command: streamlit run app.py`).
- Deploy é SNAPSHOT: copia a pasta do Workspace e reinicia o app.
- Migrações de schema ficam em `ensure_cadastro_tables()` (`@st.cache_resource`)
  e só rodam quando **alguém abre o app** depois do deploy — abra a URL para disparar.

## Login do Databricks CLI

O PAT do Free Edition **expira em poucas horas**. Para renovar:

```bash
databricks auth login --host https://dbc-7b532bee-e109.cloud.databricks.com --profile governanca-free
```

⚠️ O navegador padrão do Windows é o **Edge**, mas a sessão do Databricks está
no **Chrome**. O login abre no Edge e trava. Force o Chrome apontando `BROWSER`
para um wrapper que chama `"/c/Program Files/Google/Chrome/Application/chrome.exe" "$@" &`.
Com a sessão no Chrome, o OAuth completa sozinho ("Profile ... was successfully saved").

Verificar SQL fora do app: o usuário (OBO) não tem SELECT nas tabelas do SP em
`apps.ontologia_prd`/`apps.governanca_unity_catalog_prd`; use `SHOW TABLES` ou a
própria UI do app (que lê como SP).

## Convenções

- Unity Catalog: uma coluna por `ALTER TABLE … SET TAGS`; `SET TAGS` não aceita
  parâmetros (quoting manual com escaping); chaves de tag são case-sensitive.
- Commitar direto na `main` (é o fluxo do repo). Push para o GitHub quando pedido.

---

## Histórico de sessões / decisões

- **2026-08-28** (mais tarde) — Ajustes no glossário: **Glossário de Negócio**
  enxuto (sem Classificação/Observações — agora só de Indicador); "Objetivo" →
  **Definição** (coluna no banco segue `objetivo`). **Palavras-chave em chips**
  (digita+Enter). **Power Steward** no Indicador: nova flag `power_steward` em
  `permissoes` (checkbox em Usuários & Permissões) + nova coluna
  `power_steward` em `indicadores`; primeiro campo do form, dropdown dos
  usuários com a flag. Tela vazia mostra "Nenhum … cadastrado ainda.".
  **Bug antigo corrigido**: o editor de glossário/indicador não recarregava os
  campos ao trocar o "Registro" — agora as keys dos widgets levam o id do
  registro (`_{rk}`). Commits até `872159b`.
- **2026-08-28** — Módulo de glossário **dividido em duas telas/tabelas**:
  "Termos de Negócio (edição)" (com seletor de tipo) virou **Glossário de
  Negócio** (`apps.ontologia_<env>.glossario_negocio`) e **Indicador**
  (`.indicadores`) no menu Cadastros. `page_termos_negocio` → helper
  `_render_glossario_editor(is_indicador, ont_table, ...)` +
  `page_glossario_negocio` / `page_indicadores`. `ensure_cadastro_tables`
  migra `termos_negocio` por tipo (idempotente) e **dropa** a origem.
  `list_termos_negocio` virou `UNION ALL` das duas — a tela de consulta
  (grupo Glossário) e a tool `termos_de_negocio` do Assistente seguem iguais.
  Commit `9b3cdb5`. Publicado em PROD (deploy anterior tinha revertido o app
  pra versão antiga de 1815 linhas — restaurado). Indicador "Giro de Estoque"
  migrado OK. Docs: `docs-produto/10`, `08`, `13`.
