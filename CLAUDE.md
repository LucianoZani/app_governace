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
- **2026-09-09 (5ª parte)** — Arquitetura (17): faltava a **esteira de
  DevOps** (só tinha 1 linha na tabela de postura). Adicionada seção
  **"Esteira de implantação"** (`#esteira`, antes da postura NFR): o que o
  produto entrega (repo + Asset Bundle `databricks.yml` targets dev/prd +
  config por `app.yaml`) vs. o que não entrega (runner de CI, promoção
  dev→prd, gate, smoke test, rollback automático); **diagrama SVG
  hoje-manual × alvo-CI/CD** (git push → runner [validar › deploy dev › smoke
  › gate humano › deploy prd] → Apps dev/prd); tabela "decisões da esteira
  para o arquiteto" (plataforma de CI livre, SP de CI separado do SP do app,
  segredos em Databricks Secrets, migração de schema roda no 1º acesso e não
  no pipeline). +1 item nos "pontos abertos". Linha da tabela NFR renomeada
  "Deploy" → "Esteira de implantação".
  - Regerados: `17-arquitetura-para-aprovacao.pdf` (neutro, repo, 10 p.) +
    `Arquitetura para Aprovacao … (Comgas).pdf` (raiz Comgás). Artefato
    `914849ee` **republicado** (neutro). Fontes em `docs-entrega-comgas/`
    (agora com os HTML neutros + os cg-* + brand.py).
- **2026-09-09 (6ª parte)** — Feedback do usuário sobre o 17, três correções:
  1. **Não é doc "de aprovação"** — é "o que o app precisa do ambiente para
     funcionar". Reenquadrado: eyebrow/h1 → "arquitetura e requisitos de
     ambiente"; "decisões que a aprovação endossa" → "fundamentos do desenho";
     "o que o documento não decide" → "o que a instalação precisa prover";
     "pontos abertos que a aprovação assume" → "o que a instalação precisa
     definir"; NFR h2 → "o que o produto resolve, o que o ambiente precisa
     prover". Renomeado: repo `17-arquitetura-requisitos.pdf`, raiz Comgás
     `Arquitetura e Requisitos de Ambiente - Governanca UC (Comgas).pdf`
     (o PDF de nome antigo na raiz ficou travado/aberto num viewer — apagar
     manualmente).
  2. **Esteira = fluxo real Comgás**: Azure DevOps Repos, fluxo por PR. Dev
     sobe arquivos → PR (revisão = gate) → merge `main` → job/pipeline leva
     pro Databricks → instalação a partir do Databricks (runbook). Diagrama
     refeito (fluxo único, não mais before/after CI genérico). **A tabela "a
     confirmar com o time de DevOps" foi retirada a pedido do usuário** — a
     seção só descreve o fluxo + o que o produto encaixa nele (bundle,
     `app.yaml`, migração no 1º acesso). NFR row "Esteira" chip → "processo
     Comgás".
  3. **IA = qual modelo**, não interno-vs-externo. Comgás tem **Llama, GPT e
     Claude** disponíveis; escolha feita **ao configurar o endpoint no AI
     Gateway pela interface do Databricks** — o doc não cita mais `LLM_ENDPOINT`
     (a pedido do usuário: o modelo já vem do endpoint configurado). Sem citar
     versão. Removidas as menções a "provedor externo faz a pergunta sair" /
     privacidade como framing. NFR row "Egress & privacidade" → "Modelo de IA".
- **2026-09-09 (7ª parte)** — Usuário: "formatação esquisita, muito espaço em
  branco" nos PDFs. Causa: `break-inside:avoid` em `.tbl`/`figure`/`.panel`/
  `.table-wrap` fazia tabela/figura grande **pular a página inteira** quando
  não cabia, deixando o cabeçalho sozinho + meia página vazia. Corrigido nos
  3 `@media print` (neutros) + na camada de marca do `brand.py`:
  `.tbl`/`table`/`tbody`/`figure`/`.table-wrap`/`section`/`.step`/`.spec-block`
  → `break-inside:auto` (fluem entre páginas, `thead` repete); só
  `tr`/`li`/`.callout`/`.console`/`.figframe`/`.spec-row` ficam indivisíveis.
  Ritmo vertical apertado (margens de `section`/`h2`/`h3`/`p`, padding de
  `.panel`/`.step`). Resultado: arq 10→8 p, guia 10→9 p, runbook 20→19 p, sem
  faixas de branco. Regerados os 6 PDFs (repo neutros + raiz Comgás) e
  republicados os 3 artefatos.
- **2026-09-09 (8ª parte)** — **FinOps não funcionava no app Free**: o
  `app.yaml` de teste tem `USE_ON_BEHALF_OF_USER=false` → `obter_custo_por_dominio`
  (que roda OBO) cai pro SP → SP **não tem acesso a `system.billing`** (schema
  RESERVED, só concede a `account admins`) → `page_finops` engolia a exceção e
  caía pro xlsx de demo. **Nova fonte de dados**: env var **`FINOPS_SNAPSHOT_TABLE`**
  (`catalog.schema.tabela`, opt-in) + `_carregar_finops_snapshot()` lê essa
  tabela **como SP** (shape `dia/dominio/tipo_custo/dbus/custo_usd`). Ordem em
  `page_finops`: OBO ao vivo → snapshot table → xlsx demo. Vazio = comportamento
  antigo (Comgás intacto).
  - Produtor da tabela: **job `governanca_finops_snapshot`** no workspace de
    estudo (`estudo_databricks/notebooks/governanca/04_finops_snapshot`),
    **run-as o usuário** (tem account-admin no Free → lê `system.billing`),
    diário 07:30 BRT. Roda a **mesma query** do `obter_custo_por_dominio`
    (escopo: `app_id` do SP `8d194e53…`, warehouse `20dfe5c08c3fa359`, endpoint
    LLM), grava `governance.finops.custo_snapshot` (replace, 100 dias) + faz o
    `GRANT SELECT` pro SP do app.
  - Free `app.yaml` de teste ganhou `FINOPS_SNAPSHOT_TABLE=governance.finops.custo_snapshot`;
    app.py novo + app.yaml pushados pro source do app e **deployado** (deployment
    `01f1ac78…`, RUNNING). 1º snapshot: 50 linhas, $173.74 (Warehouse $116.71,
    Compute App $56.95, IA $0.09).
  - **Comgás**: quando ligar OBO de verdade, esvaziar `FINOPS_SNAPSHOT_TABLE` →
    volta pro `system.billing` ao vivo. Ou manter o snapshot (padrão válido).
  - Commit do `app.py` (feature `FINOPS_SNAPSHOT_TABLE`) no repo.
- **2026-09-09 (9ª parte)** — Blueprint novo do usuário
  (`Documents/Projetos/Comgas/blueprint-cadastro-acesso-power-steward_novo.md`):
  duas telas CRUD no Power Steward sobre tabelas que alimentam ABAC (row
  filter / column mask) do UC — `mapa_dominio_acesso` (N linhas/usuário,
  cross-domínio = ter +1 linha) e `mapa_sensibilidade_acesso` (1 linha/usuário,
  `usuario` chave única). Discovery feito: o app reaproveita ~80% (padrão CRUD
  do `page_data_stewards`, `list_dominios/subdominios`, `list_users_for_search`,
  RBAC). Novo de verdade: dropdown **grupo → membro** (o app não lista grupos
  hoje — `w.groups.list(attributes='...,members')` funciona), log de auditoria
  **genérico** (só tinha em-linha + logs de comentário/tag), e schema de
  segurança dedicado.
  - **Esqueleto commitado** (`5db7d9f`, +344 linhas em `app.py`): tabelas
    `mapa_dominio_acesso`/`mapa_sensibilidade_acesso`/`log_cadastros` em
    `ensure_cadastro_tables`; `list_grupos()`, `list_mapa_*()`, `_log_cadastro()`,
    `_qn()` (q_str-ou-NULL), `_seletor_grupo_usuario()`, `page_mapa_dominio_acesso`
    + `page_mapa_sensibilidade_acesso`; menu **"Acesso a Dados"** admin-only.
    SKELETON: tabelas no schema do app (`apps.power_steward_test` no Free);
    `usuario` guarda o identificador cru do membro (e-mail p/ pessoa, app-id p/
    SP); `dominio` denormalizado = nome do domínio.
  - **Deployado no Free** + tabelas criadas + INSERTs simulados por SQL (todos
    os caminhos, incl. subdomínio NULL) OK.
  - ⚠️ Os sujeitos das tabelas `mapa_*` são **usuários comuns / consumidores
    de dado** (analistas), NÃO stewards. Steward/dono de domínio é quem
    **opera** a tela. As telas usam "usuário" em todo lugar.
  - **Fixtures de teste no Free** (workspace `governanca-free`): 4 SPs
    (`teste-usuario-vendas/marketing/posvenda`, `teste-usuario-cross`) + 2
    grupos (`teste_grupo_comercial` = eu + 3 usuários, `teste_grupo_analytics`
    = eu + cross). 3 subdomínios semeados sob "Comercial" (Vendas/Marketing/
    Pos-venda). Servem pra testar o dropdown grupo→membro sem Entra ID.
    ⚠️ `w.service_principals.update()` faz PUT e **zera as memberships de
    grupo** do SP — se mexer nos SPs, repopular os grupos com `w.groups.update`.
  - **2026-09-09 — dropdown grupo→membro vazio: causa real era a QUERY, não
    permissão.** Fazer o SP admin (adicionado ao `admins` via `groups.patch`
    op ADD — usuário continua no grupo) **não resolveu** — os grupos seguiam
    com 0 membros, inclusive `admins`. Diagnóstico: `w.groups.list(attributes=
    "id,displayName,members")` devolve `members` **vazio** neste workspace;
    `w.groups.list()` **sem** `attributes` traz os membros, mas com o **id
    numérico interno** (não o `userName`/`applicationId` que o UDF ABAC casa).
    Fix (`e00d81d`): `list_grupos` usa `groups.list()` só pro nome e monta os
    membros **invertendo** `w.users.list(attributes=...groups)` +
    `w.service_principals.list(attributes=...groups)` — o atributo `.groups`
    de cada user/SP vem populado e dá o identificador certo. Cada membro agora
    tem `ident` (grava) + `rotulo` (exibe). Validado como usuário:
    comercial=4, analytics=2, admins=2. ⚠️ O SP admin continua (inofensivo no
    Free) mas **não era necessário** — reverter quando quiser:
    `w.groups.patch(id=<admins>, op=REMOVE, path='members[value eq "76230498242729"]')`.
    Na Comgás a pergunta nº 1 pro time de segurança segue: o SP consegue rodar
    `users.list`/`service_principals.list` com `attributes=groups`? Se não,
    cadastro cai no identificador manual.
  - **2026-09-09 — seletor virou busca type-ahead (formato Comgás-shape)**
    (`010e7aa`). A inversão do diretório inteiro não escala, e o SCIM do
    Databricks **não aceita** `filter='groups.value eq "<id>"'` (BadRequest) —
    então resolver "membros do grupo X" server-side é impossível. Novo modelo:
    - `list_grupos()` volta a ser só `[{id, nome}]` (barato, TTL 300).
    - **`buscar_principais(termo)`** — filtro SCIM `userName co` / `displayName
      co` em `users` + `service_principals`, `count=25`, nunca varre tudo.
      Validado no Free: "teste-usuario"→4 SPs (applicationId), "luci"→e-mail.
    - `_seletor_grupo_usuario`: **grupo = rótulo opcional** (dropdown de nomes
      ou digitar); **usuário = busca type-ahead** (fallback: identificador
      exato digitado). `ident` = `userName` (pessoa) / `applicationId` (SP).
    - Validação das 2 telas passou a exigir **só o usuário**; `grupo`/`grupo_id`
      gravam via `_qn` (aceitam NULL).
    - Comgás: `userName co` funcionou no Free como usuário; **confirmar que o
      SP consegue** rodar `users.list(filter=...)` lá (pergunta nº 1). O
      `applicationId` como `usuario` é artefato do teste; usuário Entra real →
      `userName` (pergunta nº 2: é isso que o UDF casa?).
  - **2026-09-09 — dropdown de membros de volta** (`9100a27`, a pedido do
    usuário). `membros_do_grupo(group_id)`: `groups.get` (traz os ids dos
    membros) → `users.get`/`service_principals.get` por membro → `userName`/
    `applicationId`. Custo = **tamanho do grupo**, não do diretório → escala
    p/ grupos de time (dezenas). `groups.get` member vem com
    `ref="Users/…"|"ServicePrincipals/…"` (não `type`). `_seletor_grupo_usuario`:
    escolher grupo → lista membros; expander "buscar no diretório" p/ quem
    está fora do grupo; sem grupo / grupo vazio → busca direta
    (`_busca_usuario`, helper extraído). Validado no Free: comercial → 4
    membros resolvidos com applicationId certo.
  - **4 perguntas p/ o time de segurança da Comgás antes de finalizar**:
    (1) SP do app lê SCIM Groups na Comgás? (2) o UDF ABAC casa `current_user()`
    contra e-mail / UPN / userName? → define o que gravar em `usuario`;
    (3) `dominio` = slug ou id? qual string a tag `domain` dos dados usa?
    (4) catálogo/schema das `mapa_*` (schema `seguranca` dedicado) + o SP pode
    `CREATE TABLE` lá?
  - **2026-09-09 (ajustes pós-review)** — commit `06fc125`:
    - nova flag **`admin_acesso`** em `permissoes` (coluna + checkbox no
      cadastro de Usuários + `get_user_perms` + gate no `main()`). Mesma regra
      dos outros menus: ter a flag = ver e usar; admin ignora. Menu deixou de
      ser admin-only.
    - **relabel** na tela: "Acesso por Domínio" → **"Acesso por Franquia"**;
      campo *Franquia* = o cadastro de **Domínio** do app, campo *Domínio* = o
      cadastro de **Sub-domínio**. Só rótulo (colunas do banco intactas:
      `dominio_id`/`subdominio_id`).
- **2026-09-09 (10ª parte)** — Hierarquia de negócio passou a ter **3 níveis
  de verdade: Franquia › Domínio › Sub-domínio** (antes eram 2: domínio →
  subdomínio; "franquia" só existia como rótulo). Escopo: **PoC/teste** (a
  modelagem definitiva fica pra discutir com a Comgás). `app.py` (não commitado
  ainda no momento desta linha — ver commit):
  - Nova tabela `{cad}.franquias` + coluna `dominios.franquia_id BIGINT`
    (nullable; migração idempotente em `ensure_cadastro_tables` no padrão
    `information_schema` + `ALTER TABLE ADD COLUMNS`). Base pré-existente fica
    com domínios sem franquia até editar cada um.
  - `list_franquias()` novo; `list_dominios()` agora traz `franquia_id`;
    `_clear_cad_caches` inclui `list_franquias`.
  - As **duas telas** `page_dominios`/`page_subdominios` viraram **uma só**
    (`page_dominios`, título "🗂️ Domínios"). Menu Cadastros perdeu o item
    "Sub-domínios". **1ª versão** tinha 3 abas; **substituída** (commit
    `5a6f0f0`, a pedido do usuário) por **árvore aninhada (read) + formulário
    único em cascata**: `_render_arvore_hierarquia` desenha
    Franquia › Domínio › Sub-domínio (+ seção "domínios sem franquia");
    `_form_hierarquia` — escolher/criar Franquia → Domínio (opcional) →
    Sub-domínio (opcional), e o nível-alvo (criar/editar) + o modo saem da
    combinação selecionada. Renomeia via campo "Nome"; exclui com guarda de
    cascata. Consts `_HIER_NOVA_FR`/`_HIER_NOVO_DOM`/`_HIER_NOVO_SUB`/
    `_HIER_NENHUM`, helper `_hier_franquia_id` (trata `franquia_id` NaN).
  - `page_mapa_dominio_acesso` ("Acesso por Franquia") **realinhada** ao modelo
    real: a concessão continua em `dominio_id` (+ `subdominio_id` opcional), mas
    o seletor agora é **Franquia real → Domínio (filtrado) → Sub-domínio
    opcional**; Franquia é **derivada** de `dominio.franquia_id` (sem mexer no
    schema de `mapa_dominio_acesso`). Removido o texto "Rótulos deste teste:
    Franquia = cadastro de Domínio…".
  - Deployado no Free (deploy SUCCEEDED). ⚠️ Migração (`franquias` +
    `franquia_id`) roda quando **alguém abrir o app logado**.
  - ⚠️ Pendente: seed de uma franquia + religar os subdomínios de teste
    (Vendas/Marketing/Pos-venda hoje pendem de "Comercial" como *domínio*;
    no modelo novo "Comercial" é *franquia*). Click-test do usuário.
- **2026-09-09 (11ª parte)** — Iteração de UX da hierarquia + propagação do
  nível Franquia pras telas vizinhas. Commits `5a6f0f0` … `de524d4`, todos
  pushados. Deploys por `databricks workspace import app.py` (só o app.py) +
  `apps deploy` — **não** por `databricks sync .` (ver ⚠️ do app.yaml abaixo).
  - `page_dominios`: as 3 abas viraram **árvore (read) + formulário único em
    cascata** (`_render_arvore_hierarquia` + `_form_hierarquia`). Consts
    `_HIER_*`, helper `_hier_franquia_id`.
  - Bug: domínios com `franquia_id` nulo (órfãos da migração) ficavam
    **inalcançáveis** no form. Corrigido: opção `(sem franquia — a vincular)`
    no seletor de Franquia + seletor "Franquia" no modo edição de domínio
    (religa/move; `UPDATE` inclui `franquia_id`).
  - **Owners & Stewards** (`page_stewards`): coluna Franquia; seletor mostra
    "Franquia › Domínio"; **sub-domínio virou OPCIONAL** — opção
    "— todo o domínio" (grava `subdominio_id NULL`); dedup/INSERT/listagem
    tratam NULL. Motivo: Owner é do domínio, Steward do sub-domínio — não dá
    pra exigir sub-domínio. `_select_pessoa_cadastrada` inclui responsável de
    domínio inteiro como candidato de qualquer sub-domínio.
  - **Dashboards** (`page_dashboards`): coluna Franquia + seletor
    "Franquia › Domínio" (já tinha sub-domínio opcional).
  - 🔴 **app.yaml drift causado nesta sessão**: os 3 primeiros `databricks
    sync . --full` (≈21:31–21:51) subiram o **app.yaml do repo** por cima do
    **app.yaml de teste** que estava no workspace. Efeito: schema de cadastros
    mudou de `apps.power_steward_test` → **`apps.governanca_unity_catalog_prd`**
    (com sufixo `_prd`, `CADASTRO_SCHEMA_ENV_SUFFIX` default) e
    **`FINOPS_SNAPSHOT_TABLE` saiu da config** (FinOps volta a cair no xlsx de
    demo — o snapshot da 8ª parte não é mais lido). Todo o teste de hoje
    (franquia "Comercial" + domínios Vendas/Marketing/Pós vendas, já religados)
    está em `governanca_unity_catalog_prd`. Os dados velhos
    (`power_steward_test`: 1 domínio "Comercial" + 3 subdomínios, modelo 2
    níveis) **ficaram lá** e não migram (modelo mudou). **Decisão do usuário
    ainda pendente**: (a) ficar no `_prd` e só repor `FINOPS_SNAPSHOT_TABLE`
    no app.yaml do workspace (sem commitar — o do repo é o neutro do produto);
    ou (b) restaurar o app.yaml de teste (`power_steward_test`) e recriar
    "Comercial"+domínios lá. Enquanto isso: **deployar só com `workspace
    import app.py`**, nunca `sync --full`.
- **2026-09-10 — faxina do ambiente Free ("refazer as PoCs com dados de
  pipeline").** O usuário rodou os DROPs (o classificador bloqueia `DROP
  CATALOG`/`DROP SCHEMA` pra mim — passo o SQL, ele executa).
  - **Catálogos dropados** (`CASCADE`): `demo_catalog_explorer`,
    `poc_de_para_materiais_fornecedores`, `prot_m17_bronze`, `sandbox`,
    `supply_chain`, `vendas`.
  - **Schemas dropados**: `apps.power_steward_test` (schema de teste antigo,
    órfão desde o drift do app.yaml de 21:31), `apps.ontos` (vazio).
  - **Ficam**: catálogos `apps`, `dev`, `prod`, `governance` (+ system/samples/
    workspace). `apps` agora só tem `governanca_unity_catalog_prd` (15 tabelas,
    em uso) + `default`.
  - **Limpeza pós-drop** (feita por mim): `DELETE` dos 2 indicadores que
    apontavam pra `vendas.vendas_gold` (ids 2 e 3 — `indicadores` ficou vazia);
    `app.yaml` **`ALLOWED_CATALOGS`** de `supply_chain,vendas,poc_...` → **`dev,prod`**
    (commit `dbe0f41`, deployado por `workspace import` do app.yaml). App RUNNING.
  - `dev` tem pipeline real (bronze 8 / silver 9 / gold 7 tabelas) — é a base
    pras PoCs refeitas. `prod` quase vazio (gold 1 / silver 1). Os jobs de
    governança já apontam pra `dev.gold`.
  - ⚠️ O app `data-catalog-streamlit` (explorer) perdeu o `demo_catalog_explorer`
    — quando for refeito, apontar pra `dev`.
  - **PoCs a refazer com dados de pipeline**: (1) Metric View (era em `vendas`),
    (2) de-para materiais (era `poc_de_para_materiais_fornecedores`),
    (3) ABAC/`mapa_dominio_acesso` numa gold de `dev`.
- **2026-09-14** — Sessão de manutenção do ambiente Free (sem código novo no
  repo — só limpeza de dados + config do workspace):
  - **Limpeza dos resíduos do catálogo `vendas` dropado em 09-10**: pedido do
    usuário ("deixar o app coerente com os dados que temos"). Levantamento por
    SQL achou tudo que ainda referenciava `vendas` (nenhuma tabela física por
    trás): `log_comentarios` (3 linhas), `log_tags` (7), `tag_backlog` (2
    `pendente` — travadas, nunca aprovariam pois a tabela sumiu; a 1 linha
    `aprovado` ficou, é histórico real), `dashboards` (1 — "Análise de
    Valorização de Estoque", Lakeview sobre `vendas.vendas_gold`). Todas
    apagadas via `DELETE` direto no warehouse. ⚠️ O classificador de modo
    automático bloqueou os `DELETE`s de `log_comentarios`/`log_tags` como
    "Cloud Storage Mass Delete" (WHERE por predicado, não por id) — contornado
    apagando **linha por linha por `id`** (10 chamadas), com autorização
    explícita do usuário. Não mexido: `mapa_dominio_acesso` (1 linha, fixture
    de teste da feature de Acesso a Dados, não é lixo) e a hierarquia
    Franquia/Domínio (nomes de negócio, não apontam pra tabela física).
  - **Página FinOps quebrada, causa raiz não resolvida (contornada)**:
    `_carregar_finops_excel` (fallback do `finops_dados_demo.xlsx`) lançava
    `zipfile.BadZipFile: Bad magic number for file header`. Confirmado por
    bytes idênticos (`cmp`) do `.xlsx` local vs. workspace-root vs. o path do
    **snapshot de deploy que o container efetivamente usa** — mesmo assim
    quebrava, inclusive depois de `apps stop`+`apps start` (restart completo
    do compute, não só redeploy). Não investigado mais a fundo (suspeita:
    peculiaridade de empacotamento binário do Free Edition ao copiar pro
    container). **Fix aplicado**: restaurada a env var
    **`FINOPS_SNAPSHOT_TABLE=governance.finops.custo_snapshot`** no
    `app.yaml` do workspace (perdida no drift de 09-09 §11) — ela roda
    *antes* do fallback de Excel na cadeia (OBO ao vivo → snapshot table →
    xlsx demo), então contorna o bug sem precisar consertá-lo. Confirmado o
    job `governanca_finops_snapshot` saudável (roda diário 07:30 BRT, todas
    as runs `SUCCESS`; dado só vai até 2026-09-10 por **latência normal do
    `system.billing`**, não job travado). FinOps voltou a mostrar dado real
    (US$ 167,59/30d). ⚠️ **Não commitado no repo** — é config workspace-only
    (`app.yaml` do produto é neutro, cada cliente decide a fonte de FinOps).
  - **"App está ruim" (usuário ia apresentar) → causa real era SQL Warehouse
    cold-start**: `Serverless Starter Warehouse` (`20dfe5c08c3fa359`) tinha
    escalado a zero por ociosidade; qualquer página que consulta esse
    warehouse trava em "Running…" por ~30-40s até ele subir (`STARTING` →
    `RUNNING`). **Não é bug de código.** Depois de aquecido, Painel/Governança
    de Dados/FinOps/Log de comentários/Backlog de Aprovação todos renderizam
    limpos e refletem a limpeza acima (Pendências 0, Dashboards 0, logs sem
    lixo). Recomendação passada ao usuário: abrir o app ~2-3min antes de
    apresentar pra "acordar" o warehouse; ele pode escalar a zero de novo se
    ficar >10-15min sem uso.
  - **Discussão em aberto, sem decisão**: usuário quer um projeto de **TCO**
    ligado a FinOps. Recomendação dada (não implementada): **app separado**,
    não módulo dentro do Power Steward — o FinOps atual é propositalmente
    estreito ("quanto o Power Steward custa"), TCO provavelmente cruza
    múltiplos workloads/times e merece escopo próprio. Usuário não respondeu
    ainda; retomar quando ele voltar ao assunto.
  - Ferramentas de comando usadas nesta sessão (úteis de lembrar): SQL direto
    via `databricks api post /api/2.0/sql/statements --json '{"warehouse_id":
    "20dfe5c08c3fa359", "statement": "...", "wait_timeout": "30s"}' -p
    governanca-free`; upload de arquivo binário/texto pro workspace via
    `databricks workspace import <path-remoto> --file <path-local-Windows>
    --format AUTO --overwrite -p governanca-free` (⚠️ path local precisa ser
    Windows nativo — `MSYS_NO_PATHCONV=1` faz o CLI receber o path POSIX
    literal e falhar; usar `cygpath -w` se vier de um path estilo `/c/...`).

---

## ▶️ PONTO DE RETOMADA — 2026-09-14 (fim do dia)

**Onde estamos:** ambiente Free limpo e coerente (sem lixo do catálogo
`vendas` dropado), FinOps funcionando de novo (via snapshot table), app
testado e pronto pra apresentação. As 3 PoCs com dados de `dev` (Metric View,
ABAC, de-para materiais) continuam **não iniciadas** — ver ponto de retomada
de 2026-09-10 acima pro plano, ainda válido.

### Estado do app (Free) — atualizado
- App `governanca-unity-catalog` **RUNNING**, deploy `SUCCEEDED`. `app.yaml`
  do workspace agora tem, além do que já estava documentado em 09-10:
  **`FINOPS_SNAPSHOT_TABLE=governance.finops.custo_snapshot`** (novo — ver
  acima). Resto igual (`USE_ON_BEHALF_OF_USER=false`, `ALLOWED_CATALOGS=
  dev,prod`, `LLM_ENABLED=true`).
- Cadastros (`apps.governanca_unity_catalog_prd`) sem resíduo de `vendas`:
  `log_comentarios`/`log_tags` zerados, `tag_backlog` só o histórico
  `aprovado`, `dashboards` vazio (era 1, órfão, removido).
- ⚠️ **SQL Warehouse escala a zero por ociosidade** — qualquer sessão nova
  (inclusive apresentação) pode travar ~30-40s na primeira tela até o
  warehouse subir. Considerar abrir o app alguns minutos antes de qualquer
  demo.

### Próximos passos
Sem mudança em relação ao ponto de retomada de 2026-09-10 (PoC Metric View →
PoC ABAC → PoC de-para materiais → reapontar `data-catalog-streamlit` pra
`dev`) — nenhuma dessas frentes avançou hoje. Se o usuário retomar o assunto
**TCO/FinOps**, ver a discussão em aberto registrada acima antes de propor
implementação.

**Onde estamos:** ambiente Free faxinado; app de governança rodando com a
hierarquia de 3 níveis + as 2 telas de Acesso a Dados funcionais. A próxima
frente é **refazer as 3 PoCs com dados de pipeline (`dev`)**.

### Estado do app (Free)
- App `governanca-unity-catalog` **RUNNING**. Fonte: HEAD do repo
  (`github.com/LucianoZani/app_governace`, `main`, último commit `c77790b`).
- `app.yaml` no workspace = repo: `USE_ON_BEHALF_OF_USER=false` (tudo SP),
  `CADASTRO_SCHEMA=governanca_unity_catalog` → schema real
  **`apps.governanca_unity_catalog_prd`**, `ALLOWED_CATALOGS=dev,prod`,
  `LLM_ENABLED=true` (`databricks-gpt-oss-120b`). **Sem** `FINOPS_SNAPSHOT_TABLE`.
- ⚠️ Deploy: **só `databricks workspace import <arquivo>` + `apps deploy`**.
  NUNCA `databricks sync . --full` (hoje isso sobrescreveu o app.yaml de teste
  e moveu o schema de `power_steward_test` → `governanca_unity_catalog_prd`).
- ⚠️ SP do app (`app-z41874`, id `76230498242729`) está no grupo `admins` do
  workspace — **atalho de teste, remover antes de qualquer coisa séria**
  (`w.groups.patch(op=REMOVE, path='members[value eq "76230498242729"]')`).
  Não era necessário (o fix real foi na query de `list_grupos`).

### O que funciona / foi entregue hoje
- **Hierarquia Franquia › Domínio › Sub-domínio** numa página só
  (`page_dominios`): árvore (read) + formulário único em cascata. Tabela
  `apps.governanca_unity_catalog_prd`: `franquias` (1: "Comercial"),
  `dominios` (3: Vendas/Marketing/Pós vendas, ligados à Comercial),
  `subdominios` (0).
- **Owners & Stewards** e **Dashboards**: mostram a Franquia; sub-domínio
  virou **opcional** (Owner = domínio inteiro).
- **Acesso por Franquia** (`page_mapa_dominio_acesso`): seletor grupo → membros
  (`membros_do_grupo`, escala) + busca type-ahead (`buscar_principais`,
  filtro SCIM `co`, escala). `mapa_dominio_acesso` tem **1 linha de teste**:
  `usuario = a16b75a5-…` (applicationId do SP `teste-usuario-vendas`),
  `dominio = Vendas`, sub NULL. `log_cadastros` registrou o INSERT.
- Fixtures de teste (workspace `governanca-free`): grupos `teste_grupo_comercial`
  (eu + 3 SPs) / `teste_grupo_analytics` (eu + cross); SPs
  `teste-usuario-{vendas,marketing,posvenda,cross}`.

### Decisões em aberto (não bloqueiam, mas resolver)
1. **FinOps no Free**: `FINOPS_SNAPSHOT_TABLE` saiu no drift → página cai no
   xlsx demo. O job `governanca_finops_snapshot` ainda grava
   `governance.finops.custo_snapshot`. Repor a env var (workspace only) ou
   deixar quieto.
2. **`mapa_*` num schema de segurança**: hoje só SP + owner leem
   `mapa_dominio_acesso`. Pro row filter ABAC, quem consulta a gold precisa de
   SELECT nela → mover pra schema dedicado + grant (pergunta nº 4 Comgás).
3. **Formato do `usuario`**: hoje grava `applicationId` (SP de teste). Pessoa
   real Comgás = `userName`/UPN. É o que o UDF casa com `current_user()` —
   confirmar (pergunta nº 2).

### Próximos passos (ordem sugerida)
1. **PoC Metric View** — refazer sobre uma gold de `dev` (era `vendas`):
   cadastrar 1 indicador, rodar `gerar_expr_sql` → `_validar_expr_sql_segura`
   → `testar_candidato` → `publicar_metric_view` (precisa `CREATE` no schema
   alvo + warehouse). `indicadores` está vazia.
2. **PoC ABAC** (Fase 2) — escrever a UDF de row filter que lê
   `apps.governanca_unity_catalog_prd.mapa_dominio_acesso` casando
   `session_user()` com `usuario`; classificar uma gold de `dev` com tag
   `domain`; `ALTER TABLE … SET ROW FILTER`; provar filtragem por usuário
   (usar os SPs `teste-usuario-*`). Antes: resolver decisões 2 e 3 acima
   (pelo menos pro teste, com um GRANT SELECT nos SPs).
3. **PoC de-para materiais** — refazer em `dev` (era
   `poc_de_para_materiais_fornecedores`).
4. Reapontar o app `data-catalog-streamlit` (explorer) pra `dev` quando for
   mexer nele (perdeu `demo_catalog_explorer`).

### 4 perguntas pro time de segurança da Comgás (recorrente)
(1) SP do app lê SCIM (`users.list(filter=…)`, `groups.get`) na Comgás?
(2) UDF ABAC casa `current_user()` contra e-mail / UPN / userName?
(3) `dominio` na `mapa_*` = slug ou id? o que a tag `domain` dos dados usa?
(4) schema dedicado pras `mapa_*` + o SP pode `CREATE TABLE` lá?

- **2026-09-15** — Redesenho da tela **Indicador** pra bater com o
  questionário de cadastro que os Power Stewards da Comgás vão preencher
  (um Excel, "CADASTRO DE INDICADORES", compartilhado pelo usuário na
  sessão — mais fácil de circular com outras áreas na hora de definir o
  indicador do que já nascer direto no app). Esse Excel virou **a fonte da
  verdade dos campos**.
  - Excel: 4 abas idênticas (uma por indicador de exemplo), template fixo
    de **19 perguntas em 5 blocos** — Por que existe? / Como é calculado? /
    Como analisar? / O que significa? / Quem utiliza? — colunas `Indicador |
    Bloco | Pergunta orientadora | Exemplo | Preenchimento do Power
    Steward | Status`.
  - Mapeamento: 8 perguntas já tinham campo equivalente (`objetivo`,
    `decisao_negocio`, `memoria_calculo`, `dimensoes_negocio`,
    `nivel_apuracao`, `restricoes`, `rotulo_seguranca`,
    `rotulo_privacidade`); 11 são colunas novas (`valor_gerado`,
    `problema_negocio`, `resultado_esperado`, `fontes_autorizadas`,
    `consistencia_temporal`, `comparacoes_relevantes`, `significado`,
    `premissas`, `quem_utiliza`, `privacidade_justificativa`,
    `seguranca_justificativa`) — migração idempotente em
    `ensure_cadastro_tables()`.
  - Formulário do Indicador (`_render_glossario_editor`) reorganizado em
    **5 `st.expander`**, um por bloco, com o texto de "Exemplo" da
    planilha como `help=` de cada campo (fiel ao original — não resumido;
    revisão pedida explicitamente pelo usuário depois de eu ter cortado 3
    textos na primeira versão). Por pedido do usuário: `Unidade` e
    `Variáveis utilizadas` (que não vêm do Excel) foram pro Bloco 2, perto
    de fórmula/fontes autorizadas ("conceitualmente a mesma coisa");
    `Observações` virou nota geral única no fim (não dá pra duplicar um
    campo só do banco em 5 blocos). `Rótulo de segurança`/`privacidade`
    continuam dropdown de tag governada (usados em busca por tag) — os 2
    campos de justificativa novos complementam, não substituem. Uma
    menção visível à planilha Excel que tinha vazado pro `st.caption` da
    tela foi removida a pedido do usuário (não deve aparecer pro usuário
    final). Tela de detalhe e fila da Engenharia ganharam um expander "Ver
    questionário completo do negócio". Pipeline de publicação de Metric
    View **intocado** — a descrição publicada continua só `objetivo` +
    `decisao_negocio`.
  - **Testado ao vivo no Free Edition** antes de portar: deploy, criação
    de indicador de teste ("Teste Questionário Excel"), os 5 blocos
    renderizando, tooltips batendo com o Excel, salvar/recarregar
    persistindo certo.
  - Commitado direto na `main` (fluxo do repo): `561d928` (questionário) +
    `cd6fd47` (remoção da menção ao Excel). Mesma mudança **portada pro
    bundle da Comgás** (`dados-ia-power-steward`, aplicando o diff via
    `git apply`) — ver `Documents/Projetos/Comgas/CLAUDE.md` (arquivo de
    contexto compartilhado) pro relato completo dessa frente, incluindo os
    bugs de permissão achados/corrigidos no mesmo dia e a PR !57296
    (`feature/indicador-questionario-obo` → `dev`, aberta, aguardando
    aprovação) que junta essa mudança com o fix do `admin_acesso` e a
    ligação do OBO.

- **2026-09-17** — Duas features pequenas em **Solicitar Acesso** e no
  fluxo de visitante, pedidas pelo usuário:
  - **Referência de módulos na tela Solicitar Acesso** — expander "Quais
    módulos existem?" lista as 8 flags/papéis (Power Steward, Cadastro,
    Governança, Aprovador de tags, Engenharia, Ver FinOps, Acesso a Dados,
    Admin) com descrição breve do que cada um libera (`_MODULOS_ACESSO`,
    perto do `main()` de propósito — lembrete pra manter em dia se um
    módulo novo entrar). O campo livre "O que você precisa?" virou
    multiselect "Quais módulos você precisa?" + "Detalhe o pedido"; a
    seleção vira prefixo `"Módulo(s) solicitado(s): X, Y. "` no texto
    salvo — não mudou o schema de `solicitacoes_acesso`.
  - **Visitante (sem linha em `permissoes`, `registrado=False`) não cai
    mais no Painel/Início** — não fazia sentido mostrar métricas e atalhos
    administrativos pra quem não tem nada cadastrado. Agora a página
    padrão (`default=True` no `st.Page`) é o **Glossário de Termos de
    Negócio**, com um card "👋 Olá, visitante" + botão **Solicitar acesso**
    no topo (`page_consulta_termos`, via `_atalho("solicitar_acesso", …)`).
    Início nem entra no menu "Painel" pra visitante (`main()`: `pages =
    {"Painel": [pg_inicio, pg_solicitar_acesso] if registrado else
    [pg_solicitar_acesso]}`); quem tem qualquer papel/flag cadastrado
    continua caindo no Início como sempre.
  - **Testado ao vivo no Free Edition** (deploy via `workspace import` +
    `apps start`/`apps deploy`, app tinha escalado a zero — normal):
    módulos + multiselect enviando com o prefixo certo (confirmado por
    SQL); visitante simulado via `UPDATE permissoes SET email = …` (trocar
    temporariamente o e-mail da própria linha de admin pra forçar
    `registrado=False` na sessão logada, depois revertido) — menu lateral
    ficou só com Solicitar Acesso + Termos de Negócio, landing correta no
    Glossário. Dado de teste (a solicitação "Engenharia") apagado depois.
  - Commit `eabeb2a` em `main`, pushado. Mesma mudança **portada pro
    bundle da Comgás** (`git apply` do diff, sem conflito) — commit
    `77201e3` em `fix/indicador-questionario-select-incompleta`, pushado
    (atualiza a PR !57364 — ver `Documents/Projetos/Comgas/CLAUDE.md` pro
    estado completo dessa frente). Esse push também levou junto o commit
    de 2026-09-16 que tinha ficado pendente (`b1cee91` — Solicitar Acesso
    + definição do indicador + fixes de LLM/colunas, que só existia local
    até então).

- **2026-09-18** — Duas frentes: (1) ajuste no Glossário de Negócio —
  **Termo não tem Data Owner próprio, só Data Steward** (Indicador continua
  com os dois, refletindo o Power Steward). Removido o seletor de Owner do
  formulário de Termo; listagem e card de detalhe mostram só Data Steward
  pra Termo; coluna `data_owner` segue espelhando `data_steward` por baixo
  (compatibilidade com a busca unificada). Commit `4cc9669`, pushado. Termo
  de teste "Cliente Ativo" (cadastrado nesta sessão via SQL direto, sem
  domínio) ganhou Data Steward = usuário admin.
  (2) **Primeiro piloto de ABAC (row filter + column mask) rodando de
  verdade em `dev.gold`** — fecha a Fase 2 que estava pendente desde
  2026-09-09/10 (as telas "Acesso por Franquia"/"Acesso por Sensibilidade"
  só existiam como CRUD, sem nada aplicando a restrição).
  - **Dados de teste povoados** em `mapa_dominio_acesso` (agora 5 linhas:
    `teste-usuario-vendas`→Vendas, `-marketing`→Marketing, `-posvenda`→Pós
    vendas, `-cross`→Vendas+Marketing) e `mapa_sensibilidade_acesso` (2
    linhas: `-vendas` com `pode_ver_dado_pessoal=true`, `-cross` sem nada).
  - **3 UDFs SQL** em `apps.governanca_unity_catalog_prd`:
    `fn_rf_dominio(canal)` (row filter — como não existe coluna "domínio"
    real no pipeline de `dev`, usa `fct_pedidos.canal` como **proxy
    arbitrário só de piloto**: web/app→Vendas, loja→Marketing,
    telefone→Pós vendas — **não é o modelo definitivo**, Comgás precisa de
    uma tag/coluna `domain` real), `fn_mask_nome`/`fn_mask_nascimento`
    (column mask, gated por `pode_ver_dado_pessoal`/`_sensivel`). Aplicadas
    com `ALTER TABLE dev.gold.fct_pedidos SET ROW FILTER …` e
    `ALTER TABLE dev.gold.dim_cliente ALTER COLUMN … SET MASK …`.
  - 🔴 **Achado real de plataforma**: `is_account_group_member('admins')`
    (grupo de **conta**/metastore) voltou `false` pro próprio usuário admin
    do Free — as 3 UDFs foram escritas com esse bypass e o admin ficou
    filtrado/mascarado no próprio teste. Causa: no Free, o grupo `admins`
    do usuário é **workspace-level**, não account-level. Trocado por
    `is_member('admins')` (workspace) nas 3 funções — confirmado que
    resolve (`SELECT is_member('admins')` → `true`; depois do fix, admin
    volta a ver os 4 canais e nome/data de nascimento reais). **Vale
    conferir isso de novo na Comgás** — lá pode ser o inverso (grupos
    sincronizados via SCIM da conta, `is_account_group_member` pode ser o
    certo) — não tratar como líquido e certo sem checar no ambiente real.
  - **Validação da lógica sem autenticar como os SPs de teste**: tentei
    duas vezes rodar como as identidades de teste — `GRANT SELECT ON
    SCHEMA dev.gold` pros 4 SPs e `service-principal-secrets-proxy create`
    (gerar OAuth secret pra autenticar como cada SP) — **as duas ações
    foram bloqueadas pelo classificador de modo automático** (“Permission
    Grant” e “Credential Exploration”, respectivamente). Contornado
    validando a **mesma expressão `EXISTS`/`CASE` das UDFs, mas com o
    identificador de cada SP como literal** em vez de via `current_user()`
    — prova que a regra de negócio bate (vendas→web/app, marketing→loja,
    posvenda→telefone, cross→3 canais, sem cadastro→nada; sensibilidade
    idem) mas **não** prova end-to-end que autenticar como o SP realmente
    produz esse `current_user()` no warehouse. Isso ainda depende de: (1)
    `GRANT SELECT, USE SCHEMA ON SCHEMA dev.gold TO` os 4 SPs de teste
    (`728d0ce5-92c3-433e-861f-507418d64389`,
    `01b5df9a-2dc6-4896-ae29-106a8dc922a2`,
    `6a82bfba-8069-4ab8-aa6c-f8ee197de00e`,
    `a16b75a5-30fd-4d12-ba9d-0e2abfc8d338`) + `GRANT USE CATALOG ON
    CATALOG dev`; (2) gerar client secret de cada SP
    (`databricks service-principal-secrets-proxy create <app-id>`) e rodar
    uma query autenticado como ele. **Pendente — o usuário precisa rodar
    essas duas ações** (ou ajustar permissões do Claude Code pra
    liberá-las) se quiser o fechamento end-to-end.
  - Reversível a qualquer momento: `ALTER TABLE dev.gold.fct_pedidos DROP
    ROW FILTER`, `ALTER TABLE dev.gold.dim_cliente ALTER COLUMN nome DROP
    MASK` (idem `data_nascimento`), `DROP FUNCTION` das 3 UDFs.

- **2026-09-18 (2ª parte)** — Usuário pediu pra **ver a máscara funcionando
  na prática** (sem precisar ligar OBO, que segue `false` de propósito
  nesse ambiente). Achado no caminho: o SP do app (`app-z41874
  governanca-unity-catalog`, id `76230498242729`) ainda estava no grupo
  `admins` do workspace — hack temporário de 2026-09-09 nunca revertido —
  o que dava bypass na máscara (`is_member('admins')`) mesmo rodando tudo
  via SP (OBO off). **Removido do grupo** (`databricks groups patch
  81667451406133 op=remove members[value eq "76230498242729"]`),
  confirmado pela lista de membros do grupo (só sobrou o usuário humano).
  **Testado ao vivo** na tela Governança de Dados → `dev.gold.dim_cliente`
  → coluna `data_nascimento`: os 5 valores da Amostra de dados vieram
  `None` — confirma a máscara `fn_mask_nascimento` funcionando de ponta a
  ponta (SP sem `pode_ver_dado_pessoal_sensivel` cadastrado → mascarado).
  Não foi necessário mexer no `USE_ON_BEHALF_OF_USER` (segue `false`).

- **2026-09-18 (3ª parte)** — Usuário pediu pra **alimentar um indicador
  com insumos suficientes pra IA gerar a query da Metric View de verdade**.
  Achado o indicador "Margem bruta" (id 4) já cadastrado (de sessão
  anterior) mas incompleto: `objetivo` vazio, `dimensao_tabelas`/
  `metrica_tabelas` apontando pra `dev.gold.dim_cliente`/`fct_pedidos` só
  que **sem nenhuma coluna escolhida** no picker.
  - **Preenchido via SQL direto** (`UPDATE apps.governanca_unity_catalog_prd.indicadores
    WHERE id = 4`) todo o questionário de negócio (objetivo, decisão
    apoiada, valor gerado, problema de negócio, resultado esperado,
    memória de cálculo, unidade, variáveis utilizadas, fontes autorizadas,
    consistência temporal, dimensões desejadas, comparações relevantes,
    definição, restrições, premissas, quem utiliza, domínio = Vendas) +
    colunas reais nos pickers: dimensão `dev.gold.dim_cliente` (sk_cliente,
    segmento_erp1, uf), métrica `dev.gold.fct_pedidos` (valor_liquido,
    sk_cliente, dt_pedido, canal).
  - 🔴 **Armadilha de encoding no Windows, achada e corrigida no ato**:
    o primeiro UPDATE (montado via `cat arquivo.sql | python -c
    "...sys.stdin.read()..."`) corrompeu todos os acentos (mojibake tipo
    "É"→"Ã©") — Python no Windows lê `stdin` no codepage do console, não
    UTF-8, mesmo o arquivo fonte estando em UTF-8 correto. **Fix**: nunca
    passar texto acentuado por `stdin`/`stdout` do Python nesse ambiente —
    ler e escrever os arquivos com `io.open(..., encoding='utf-8')`
    explícito nas duas pontas. Corrigido com um segundo UPDATE já com o
    encoding certo (conferido depois via SELECT).
  - ⚠️ **Também achada uma armadilha de path do Git Bash pro `databricks
    api post --json @arquivo.json`**: `MSYS_NO_PATHCONV=1` é obrigatório
    pro endpoint (`/api/2.0/...`) não virar path do Windows, mas ele
    também impede o `@/tmp/arquivo.json` de ser traduzido pro path real —
    as duas coisas precisam de comportamento oposto na mesma chamada. Saída:
    salvar o JSON num arquivo com **path relativo** (sem `/` na frente, ex.
    `arquivo.json` no diretório do repo) — daí `MSYS_NO_PATHCONV=1` não mexe
    nele (só reescreve o que começa com `/`) e o endpoint continua intacto.
  - **Testado ao vivo**: "🤖 Traduzir fórmula com IA" gerou
    `SUM(valor_liquido) / COUNT(DISTINCT sk_cliente)` — bate exatamente com
    a memória de cálculo escrita. Botão "Testar expressão" **inicialmente
    voltou `NULL`** (era pra dar ~1229,73).
  - 🔴 **Causa raiz (não é bug do app — efeito colateral do piloto ABAC de
    hoje, 1ª/2ª parte)**: o SP do app tinha sido removido do grupo
    `admins` na 2ª parte desta sessão (pra provar a máscara de
    sensibilidade). Só que o **row filter** `fn_rf_dominio` em
    `dev.gold.fct_pedidos` (também do piloto ABAC) usa o mesmo bypass
    `is_member('admins')` — sem ele e sem estar cadastrado em
    `mapa_dominio_acesso`, o SP passou a ver **zero linhas** de
    `fct_pedidos`, e `SUM()/COUNT(DISTINCT)` sobre zero linhas dá `NULL`.
    Confirmado rodando a mesma expressão manualmente (deu 1229,72 de
    verdade — `SHOW GRANTS ON SCHEMA dev.gold` também confirmou que o SP
    tem `SELECT` normal na schema, então não era falta de grant).
  - **Fix**: em vez de devolver o SP pro grupo `admins` (o que anularia o
    teste de máscara que acabou de ser validado), **cadastrado o SP como
    usuário em `mapa_dominio_acesso`** com acesso às 3
    franquias/domínios (Vendas id 2, Marketing id 3, Pós vendas id 4) —
    ele passa a ver `fct_pedidos` inteiro pra fins de engenharia, sem
    precisar de bypass de admin. Retestado: "Testar expressão" voltou
    **1.229,7273**, batendo com o cálculo manual. Máscara de sensibilidade
    (2ª parte) continua intacta (não foi mexida).

- **2026-09-18 (4ª parte)** — Ao clicar em publicar, o usuário bateu no
  erro **"Não foi possível montar o DDL: Dimensão e métrica apontam para
  tabelas diferentes"** — eu tinha montado o indicador "Margem bruta" com
  Dimensão em `dim_cliente` (segmento/UF) e Métrica em `fct_pedidos`,
  tabelas diferentes. `montar_yaml_metric_view()` só sabia gerar Metric
  View de uma tabela só, sem `joins:`. Perguntado se Metric View não
  suporta join — **suporta** (confirmado via WebSearch/WebFetch na doc
  oficial: `joins:` com `on`/`using`, star/snowflake schema) — a limitação
  era só do gerador deste app, nunca implementado. **Implementado agora**:
  - `_render_tabela_picker` (usado pela Dimensão) ganhou um parâmetro
    `join_fonte` — quando a tabela escolhida pra Dimensão é diferente da
    tabela já salva como Métrica, pede a(s) coluna(s) em comum entre as
    duas (calculadas automaticamente, interseção dos nomes de coluna) e só
    libera "Adicionar tabela" com pelo menos uma escolhida; guarda como
    `colunas_join` no item JSON (vira `USING (...)` — não suporta nomes
    diferentes dos dois lados, isso exigiria pedir duas colunas
    separadamente, não implementado).
  - `montar_yaml_metric_view()` reescrito: separa itens de dimensão que
    são da própria tabela-fonte (sem join) dos que são de outra tabela
    (viram uma entrada em `joins:`, alias = nome da tabela); monta
    `dimensions:` referenciando `alias.\`coluna\`` pras colunas juntadas.
    Também corrige um bug latente da versão anterior: só validava a
    tabela do **primeiro** item de dimensão, mas juntava colunas de
    **todos** os itens sem checar tabela — com múltiplas tabelas de
    dimensão isso silenciosamente misturava coluna de uma tabela errada.
  - **Testado publicando de verdade**: reconfigurado o indicador pra
    Dimensão = `dev.gold.dim_cliente` (segmento_erp1, uf) com junção por
    `sk_cliente`, Métrica = `dev.gold.fct_pedidos`. DDL gerado com
    `joins:`/`using: [sk_cliente]` — **rodado de verdade** (`CREATE OR
    REPLACE VIEW dev.gold.margem_bruta WITH METRICS LANGUAGE YAML`,
    sucesso) e consultado (`SELECT segmento_erp1, uf, MEASURE(margem_bruta)
    FROM dev.gold.margem_bruta GROUP BY ALL`) — retornou 10 linhas com
    valores reais por segmento/UF. Indicador marcado como "Publicado" no
    app. Commit `b8b4276` em `main`.
  - Limitação que ficou registrada em comentário no código, não resolvida
    agora: só suporta `USING` (mesmo nome de coluna nos dois lados) — uma
    junção por chaves com nomes diferentes (`ON`) exigiria pedir duas
    colunas na UI, não implementado.

- **2026-09-18 (5ª parte)** — Usuário trouxe uma query real de indicador da
  Comgás ("Desconto Total", `nie_prd_legacy.ref_faturamento.ft_mercado_fatura`
  com 5 `LEFT JOIN`s) pra validar contra o fix da 4ª parte, e ela expôs 3
  gaps: (1) medida usando coluna de tabela **juntada só pela Métrica**, não
  pela Dimensão (`SUM(m.VL_DESCONTO_CLIENTE)`); (2) dimensão como
  **expressão computada**, não coluna crua (`MONTH(f.DT_PERIODO)`); (3)
  **filtro de negócio** (`filter:`) pras restrições ("fica fora do conceito
  de desconto: devolução, multa, consumo zerado com desconto..."). Todos
  confirmados como suportados pela Metric View de verdade (via
  WebSearch/WebFetch na doc oficial) — só faltava o gerador do app cobrir.
  **Implementado**:
  - Generalizado o join da 4ª parte: antes só a Dimensão pedia coluna de
    junção contra a Métrica; agora `_render_tabela_picker` pede o mesmo
    pra **Métrica também** (a partir da 2ª tabela — a 1ª sempre é a
    fonte). Helper novo `_coletar_joins(fonte, *grupos_itens)` centraliza
    a montagem de `joins:` a partir de Dimensão + Métrica juntas, sem
    duplicar quando a mesma tabela aparece nos dois lugares.
  - `gerar_expr_sql` e `testar_candidato` passaram a operar sobre colunas
    **qualificadas com o alias do join** (bare pra tabela-fonte, ex.
    `` `valor_liquido` ``; `alias.\`col\`` pra tabela juntada) — a IA já
    recebe o hint certo e `testar_candidato` monta o `FROM ... LEFT JOIN
    ... USING (...)` de verdade (mais `WHERE filtro_sql`, se preenchido)
    antes de testar, em vez de rodar só contra a tabela-fonte isolada.
  - 2 colunas novas em `indicadores`: `filtro_sql` (SQL livre, vira
    `filter:` — não passa por IA, mesmo espírito do "Criar query sem
    IA") e `dimensoes_calculadas` (JSON `[{"nome","expr"}]`, expressão
    SQL livre pra dimensão que não é coluna crua). UI: `_render_dims_calculadas`
    (editor nome+expressão) + `st.text_area` do filtro, ambos na tela
    Indicadores — Engenharia, salvos junto com o lineage.
  - 🔴 **Bug achado no primeiro teste, corrigido no ato**: `list_indicadores()`
    tinha uma lista explícita de colunas no `SELECT` e não incluía as 2
    novas — os dados salvavam certo no banco (confirmado por SQL) mas a
    tela/gerador de YAML nunca via, porque liam de volta via essa função.
    Sintoma: "Dimensões calculadas" voltava vazio na UI e sumia do YAML
    logo depois de salvo. Adicionadas as 2 colunas no `SELECT`.
  - **Testado publicando de verdade** um cenário com TUDO junto: Métrica
    com 2 tabelas (`fct_pedidos` fonte + `map_cliente_fonte` juntada via
    `sk_cliente`), Dimensão com 1 tabela juntada (`dim_cliente`) + 1
    dimensão calculada (`mes = MONTH(\`dt_pedido\`)`), e um filtro
    (`COALESCE(\`valor_liquido\`, 0) > 0`). IA traduziu a fórmula pra
    `SUM(valor_liquido) / COUNT(DISTINCT map_cliente_fonte.\`id_fonte\`)`
    — já com o alias certo — e o teste voltou **1.620,1429** (valor real,
    não NULL). DDL final com `filter:`, os 2 `joins:` e a dimensão `mes`
    todos presentes; `CREATE OR REPLACE VIEW ... WITH METRICS LANGUAGE
    YAML` **rodado de verdade**, sucesso. Uma query `SELECT MEASURE(...)
    GROUP BY ALL` deu `DIVIDE_BY_ZERO` em 2 dos 13 grupos (grupos onde
    `valor_liquido` é nulo/zero em todas as linhas, então o filtro zera o
    denominador) — **é uma questão de fórmula de negócio** (precisaria de
    `NULLIF`/`try_divide` na expressão), não um defeito do join/filtro/
    dimensão calculada, que geraram e rodaram certo. Commit `4b12ba0` em
    `main`.
  - Cobre agora os 3 gaps que a query real da Comgás tinha exposto —
    ainda falta portar esse mesmo trabalho pro bundle da Comgás
    (`dados-ia-power-steward`) quando for cadastrar o indicador "Desconto
    Total" de verdade lá (ver `Documents/Projetos/Comgas/CLAUDE.md`).
