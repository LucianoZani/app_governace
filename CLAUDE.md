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

- **2026-08-28** — `app.py` da variante **`-comgas`** sincronizado com esta
  versão. As únicas diferenças de código do fork Comgás (reaplicadas):
  `CAD_SCHEMA` sem sufixo `_<env>` (schema fixo via `CADASTRO_SCHEMA`);
  `ONTOLOGIA_SCHEMA = os.environ.get("ONTOLOGIA_SCHEMA", CAD_SCHEMA)`; marca
  "Power Steward" em `page_governanca` e `set_page_config`. `app.yaml`/
  `databricks.yml`/`docs` do `-comgas` NÃO foram tocados. A pasta `-comgas`
  não é repo git. Deploy do Comgás é em outro workspace (Azure), separado.
- **2026-08-28** (fim) — Nova **tela de Início** (`page_inicio`, menu "Painel",
  `default=True` — tirado de `page_governanca`). Painel adaptado por papel:
  cabeçalho c/ resumo, `st.metric` (delta via `_novos_na_semana`), pendências
  de aprovação, saúde dos cadastros (`_lacunas_cadastro`), meus indicadores
  (Power Steward), atividade recente (`_atividade_recente`), atalhos
  (`_atalho` + `st.session_state["_nav_pages"]`). `get_user_perms` passou a
  devolver `power_steward`; `main()` grava `st.session_state["perms"]`. PoC
  visual aprovada antes (artifact). Doc: `docs-produto/14-tela-inicio.md`.
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
- **2026-08-28 → 2026-09-07** — ⚠️ Entre essas datas o app foi iterado
  **direto no workspace via `databricks sync`, sem commit**. O repo ficou
  ~1290 linhas atrás do que estava no ar.
- **2026-09-07** — Pasta do repo reorganizada para
  `Documents/Projetos/Comgas/poc-metric-view-freeedition/`. Três commits,
  todos pushados para `origin/main`:
  - `dd11be6` **sync: traz de volta ao git a versão publicada** (fonte da
    verdade confirmada). Novidades que estavam só no ar:
    - **Módulo FinOps** — `page_finops`, `_render_finops_dashboard`,
      `obter_custo_por_dominio` (lê `system.billing`),
      `_carregar_finops_excel` (fallback `finops_dados_demo.xlsx`);
      `openpyxl` no `requirements.txt`; flag de permissão `ver_finops`.
    - **Módulo Indicadores-Engenharia** — `page_indicadores_engenharia`:
      pipeline Indicador → **Metric View** (`gerar_expr_sql` = IA traduz a
      fórmula p/ SQL, `montar_ddl`/`yaml_metric_view`,
      `_validar_expr_sql_segura`, `_status_publicacao_pos_lineage`,
      handoff negócio→engenharia).
    - Helpers de busca: `search_by_tag`, `search_columns`,
      `list_tables_with_comment`.
    - Não tocou `README.md`/`app.yaml`/`databricks.yml`. Docs de FinOps e
      Engenharia ainda por escrever.
  - `62c9bfd` **worklist "Revisar catalogação feita com IA"** — env var
    **`PROPOSTAS_IA_TABLE`** (`catalog.schema.tabela`, opt-in). Toggle na
    página Governança de Dados que lista tabelas com descrição de coluna
    sugerida por IA e `status='pendente'` (respeita `ALLOWED_CATALOGS`).
    "Abrir" pré-seleciona a tabela no editor (`_gov_preset` one-shot;
    selectboxes agora com key `gov_sel_cat/sch/tbl`). Ao salvar o comentário
    de uma coluna com proposta, `apply_changes` → `_marcar_proposta_ia_revisada`
    (MERGE `status` → `aprovado`/`ajustado` + `revisado_por`/`revisado_em`/
    `aplicado_em`; best-effort, OBO, nunca bloqueia). Helper `q_fqn()`.
    Docs: `docs-produto/05`, `07`, `13`.
  - `0588046` **painel "Catalogação sugerida por IA" (aplicar em bloco)** —
    com a tabela aberta, lista as colunas com proposta pendente; botão
    **"Revisado — aplicar as N descrições"** grava o texto da IA como
    `COMMENT ON COLUMN` de todas de uma vez e fecha as linhas; expander
    "Ajustar…" (`st.data_editor`) pra corrigir antes. Aparece sempre que a
    tabela tem pendência (independe do toggle). Helpers:
    `_propostas_pendentes_da_tabela`, `_aplicar_revisao_ia`,
    `_render_revisao_ia_tabela`. Docs: `docs-produto/07`, `13`.
- **2026-09-08** — Só documentação:
  - Histórico deste `CLAUDE.md` posto em dia (entradas 08-28→09-07). Commit
    `c67fabe`.
  - **Runbook de implantação** (artefato **"Runbook Governança UC"**,
    `https://claude.ai/code/artifact/11a23162-3362-44d0-a8d0-559a447fd5de`;
    exportação em `16-runbook-implantacao.pdf` na raiz do repo, **não
    versionada**) — limpo para ser entregue a um cliente como o runbook **do**
    app, sem sugerir que existem outras versões: eyebrow "Runbook de
    implantação · modelo canônico" → "Runbook de implantação"; removida a
    menção a "escrita para qualquer workspace" e o chip "Multi-workspace". O
    corpo já era neutro (placeholders `<...>`, sem citar cliente / ambiente de
    teste / fork). O `16-runbook-implantacao.pdf` na raiz foi reexportado
    (Chrome headless `--print-to-pdf` do HTML do artefato — `pdftoppm`/`pypdf`
    não instalados nesta máquina, então sem conferência visual). ⚠️ Viewers do
    link do artefato veem uma versão fixada anterior — re-fixar pelo menu de
    compartilhamento.
- **2026-09-09** — Só o PDF do runbook: estava **mal formatado** porque o
  artefato não tinha CSS de impressão (o `--print-to-pdf` do Chrome quebrava
  o layout — rail de navegação virava tira cortada, blocos `console` perdiam
  o fundo escuro, badges/callouts/thead sem cor, comandos de CLI e diagrama
  cortados na margem, passos/tabelas partidos entre páginas).
  - Adicionado bloco **`@media print`** ao HTML do artefato (não muda nada na
    tela): `print-color-adjust:exact`; `nav.rail` escondido; `.shell` em
    coluna única largura cheia; `.console pre` com `white-space:pre-wrap`
    (comandos quebram em vez de sumir); tabelas sem clipping + células
    quebram linha; `.diagram-row` empilha na vertical; `break-inside:avoid`
    em console/callout/tabela/linha, `auto` em `.step`/`.spec-block`/`section`
    (evita páginas meio vazias).
  - `16-runbook-implantacao.pdf` na raiz **reexportado** (Chrome headless
    `--print-to-pdf`), 21 páginas. Desta vez **com conferência visual**:
    `pymupdf` (=`import fitz`, está instalado nesta máquina — ao contrário de
    `pdftoppm`) renderiza as páginas em PNG. Revisado página a página, sem
    conteúdo cortado. Continua **não versionado**.
  - Artefato **republicado** na mesma URL (`11a23162…`, label "CSS de
    impressão para o PDF") — aparência na tela idêntica. ⚠️ Segue pendente
    **re-fixar a versão** pelo menu de compartilhamento (viewers ainda veem a
    fixada anterior).
- **2026-09-09 (2ª parte)** — Novo artefato **"Arquitetura Governança UC"**
  (`https://claude.ai/code/artifact/914849ee-9f3f-4211-b58b-06efb9d1d51b`,
  favicon 📐) — **recorte de arquitetura para aprovação de desenho** por um
  arquiteto, derivado do runbook mas focado: o runbook lidera com o passo a
  passo de deploy (B1–B8 + specs), o arquiteto precisa do desenho e das
  lacunas. Seções: "o desenho em uma tela" (decisões que a aprovação endossa
  + o que o doc não decide), arquitetura de execução (com **diagrama SVG** de
  fluxo de requisição + fronteira de confiança — navegador → app → 2
  identidades OBO/SP → warehouse → catálogos de negócio / schema interno; a
  única aresta de escrita fora do schema do app destacada), modelo de
  identidade (tabela condensada, 9 linhas), integração com a plataforma,
  pegada no ambiente, **postura NFR** (tabela dimensão × situação com chips
  `no desenho`/`configurar`/`não coberto` — observabilidade, rollback,
  retenção, disponibilidade marcados como gap), e "pontos abertos que a
  aprovação assume" (7 itens). Tipografia Newsreader (títulos) + IBM Plex
  Sans/Mono; tema claro/escuro; tem `@media print`.
  - Export **`17-arquitetura-para-aprovacao.pdf`** na raiz (9 páginas, Chrome
    headless, conferido com `pymupdf`). **Não versionado**, como o do runbook.
  - Conteúdo 100% do produto principal (este repo) — sem citar cliente. Fatos
    tirados do próprio runbook + `app.py`.
- **2026-09-09 (3ª parte)** — Terceiro artefato: **"Guia de Uso Governança
  UC"** (`https://claude.ai/code/artifact/cc09b782-6963-4878-87e6-b08750c3c77f`,
  favicon 📘) — **guia de utilização** do app (documentação de referência pra
  guardar/consultar). O usuário vai entregar os 3 juntos: **17 = estudo da
  reunião** de aprovação; runbook + guia de uso = documentação do app.
  - Compilado do `docs-produto/` + `app.py`. Escopo decidido pelo usuário:
    **só os módulos de uso**, **sem o que o runbook já cobre** (fora
    instalação/config/grants). Seções: o que o app faz · o que você vê
    depende do papel (papéis + flags, visão de *uso* do RBAC) · Painel ·
    Governança de Dados · Revisar catalogação por IA · Cadastros ·
    Glossário/Indicadores · **Indicadores—Engenharia** (fluxo Metric View em
    4 passos, tirado do `app.py`) · Assistente IA · **FinOps** (tirado do
    `app.py` — nenhum dos dois tinha doc em `docs-produto/`) · problemas
    comuns (subset voltado ao usuário final).
  - Mesma identidade visual do 17 (Newsreader + IBM Plex, claro/escuro,
    `@media print`, `pymupdf` pra conferir). Export
    **`18-guia-de-utilizacao.pdf`** na raiz, 10 páginas, **não versionado**.
  - Label do campo `macroprocesso` sai como **"Franquia"** (o `app.py` deste
    repo já tem o relabel — `docs-produto/10` ainda diz "Macroprocesso").
  - ⚠️ `docs-produto/` está desatualizado: não tem FinOps, Metric
    View/Engenharia nem a worklist de catalogação por IA; índice do
    `docs-produto/README.md` para em 13 (sem o 14). Vale escrever esses
    capítulos um dia.
- **2026-09-09 (4ª parte)** — Os 3 documentos (16/17/18) **rebrandados na
  identidade Comgás** pra entrega. Template de referência:
  `Documents/Projetos/Comgas/Slides_Metodo_Arquitetura_Scale_Runner_Comgas.pptx`
  (deck gerado por IA já no estilo Comgás — extraí paleta + logo dele via
  `zipfile`/PIL: azul `#0078B0`, verde `#78C040`, navy de tabela `#024788`,
  laranja `#F5821F`; logo `image2.png` embutido como data-URI).
  - Método: `docs-entrega-comgas/brand.py` aplica uma **camada de marca** no
    fim do `<style>` de cada HTML (troca tokens → paleta Comgás, fontes →
    **Montserrat** títulos + **Lato** corpo, `thead` azul-marinho, logo no
    header, rodapé "Comgás | Governança de Dados | …"). Quase tudo é
    token-driven, então recoloriu inclusive o diagrama SVG sozinho.
  - **PDFs Comgás na raiz `Documents/Projetos/Comgas/`** (fora do repo):
    `Arquitetura para Aprovacao …`, `Runbook de Implantacao …`,
    `Guia de Utilizacao … (Comgas).pdf`. Fontes de build (cg-*.html + brand.py
    + logo b64) em `docs-entrega-comgas/`.
  - Os `16/17/18-*.pdf` no repo seguem sendo as versões **neutras** (sem
    marca). Os **artefatos** (claude.ai) também seguem neutros — não
    republicados; se quiser alinhar, rodar o cg-*.html por cima de cada URL.
