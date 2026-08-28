# 14. Tela — Início (painel)

Primeira tela do app (menu **Painel → Início**, é a que abre por padrão). É um
painel de orientação: "o que preciso fazer" + "como está a governança", com
**blocos que aparecem conforme o papel e as flags** do usuário
([06. Permissões](./06-permissoes.md)).

## Blocos e para quem aparecem

| Bloco | Aparece para | O que mostra |
|---|---|---|
| **Cabeçalho** | todos | "Olá, `<nome>`" (displayName do workspace/conta, ou heurística sobre o e-mail; **"Olá, visitante"** se o usuário não estiver em `permissoes`), chips do papel/flags e uma frase-resumo ("Você tem N tags aguardando aprovação e M lacunas de cadastro"; para visitante, um aviso pra pedir acesso a um admin). |
| **Números** | todos | 6 `st.metric`: domínios, sub-domínios, owners/stewards, termos, indicadores, dashboards — com `delta` do que foi criado nos últimos 7 dias. |
| **⏳ Pendências de aprovação** | admin ou `aprovador_tags` | Contagem + as 5 tags mais recentes no backlog + atalho para o Backlog. |
| **🩺 Saúde dos cadastros** | admin ou `ver_cadastros` | Lacunas: domínios/sub-domínios sem Data Steward, indicadores sem Power Steward, termos sem definição, termos/indicadores sem domínio. Cada uma com atalho "corrigir". Se não há lacuna: "✅ Cadastros em dia." |
| **📈 Meus indicadores** | `power_steward` (ou admin que também seja) | Indicadores onde o usuário é o Power Steward (`indicadores.power_steward == e-mail`). |
| **🕓 Atividade recente** | admin ou `ver_logs` | Últimas ~8 alterações de comentário/tag (dos logs de auditoria) + "N esta semana". |
| **👋 Comece por aqui** | `leitor` | Atalho para o Glossário (consulta) e o Assistente de IA. |
| **🔗 Atalhos** | todos | Botões (`st.page_link`) para as ações comuns — filtrados por papel: só aparece o atalho de uma tela se o usuário tem acesso a ela. |

## Dados

Tudo vem das funções `list_*` **já em cache** (nenhuma varredura de catálogo). As
lacunas do bloco "Saúde" são derivadas em memória das mesmas listas. Os `delta`
dos `st.metric` vêm de `_novos_na_semana()` (`@st.cache_data(ttl=60)`, ~5
`SELECT count(*)` leves). "Cobertura de documentação" (% de tabelas comentadas)
ficou de fora do v1 porque exige varrer os catálogos via OBO — ideia de v2.

## RBAC

A tela em si é **sempre visível** (não tem gate). Cada bloco é que respeita o
papel/flags, e os atalhos só linkam para páginas que o usuário pode abrir.
