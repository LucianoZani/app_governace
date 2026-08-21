# 9. Módulo — Assistente de Governança (IA)

Painel de chat (inspirado num assistente irmão de outro produto do autor,
"Ask Ontos") que responde perguntas em linguagem natural sobre tudo o que
está cadastrado/governado no app. **Módulo opcional** — adicionado depois do
core do produto (Governança + Cadastros), desligado por padrão.

## O que faz

- Responde perguntas do tipo "quais colunas da tabela X não têm comentário?",
  "quem é o data steward do domínio Y?", "que termos de negócio existem sobre
  Z?", "o que está pendente no backlog de aprovação de tags?".
- Usa **function calling**: o modelo decide, a cada pergunta, quais das
  fontes de dados do app consultar (uma ou várias tools, em até 6 idas e
  vindas por pergunta) e formula a resposta final a partir do resultado.
- Mantém o histórico da conversa na sessão do usuário (botão "Nova conversa"
  para reiniciar).

## O que NÃO faz (por design)

**Só consulta.** Nenhuma tool de escrita é exposta ao modelo — o assistente
não aplica tag, não grava comentário, não cadastra domínio/steward/dashboard/
termo, não decide item do backlog de aprovação. Para qualquer ação, o usuário
precisa ir até a tela correspondente do app.

## Fontes de dados disponíveis ao assistente (tools)

| Tool | O que devolve |
|---|---|
| `tags_e_comentarios_da_tabela` | Comentário e, por coluna, tipo/comentário/tags de uma tabela específica (catalog/schema/table). |
| `tags_governadas_disponiveis` | Catálogo de governed tags (chaves e valores permitidos). |
| `dominios_e_subdominios` | Domínios e sub-domínios cadastrados. |
| `data_stewards` | Stewards cadastrados, com domínio/sub-domínio. |
| `dashboards_cadastrados` | Dashboards (AI/BI) cadastrados, com domínio/sub-domínio. |
| `padroes_de_dado_pessoal` | Padrões que classificam uma coluna como dado pessoal. |
| `termos_de_negocio` | Glossário de termos/indicadores (ver [10. Módulo — Glossário](./10-modulo-glossario-termos.md)). |
| `backlog_de_aprovacao_de_tags` | Itens do backlog de tags, opcionalmente filtrado por status. |
| `log_auditoria` | Log de comentários ou de tags (mais recentes primeiro, com limite). |

Todas as tools executam como **service principal** (mesma identidade que os
cadastros — o assistente não herda os grants de Unity Catalog do usuário que
está perguntando; ele só enxerga o que o SP consegue ler, incluindo tags/
comentários de tabelas dentro de `ALLOWED_CATALOGS`).

## Arquitetura do backend

O app monta um cliente compatível com a API da OpenAI (`pip install openai`),
apontando para o **Unity AI Gateway do próprio workspace**:

```
base_url = "{host-do-workspace}/ai-gateway/mlflow/v1"   # não é o /serving-endpoints clássico
```

autenticado com um token OAuth do service principal, obtido na hora de cada
chamada (não é cacheado — o token expira). O modelo consultado é o
**model service** identificado por `LLM_ENDPOINT` (`catalogo.schema.nome`),
registrado no Unity Catalog e servido pelo AI Gateway — **cada instalação
aponta para o seu próprio modelo**, o produto não traz um modelo embutido.

## Compatibilidade de modelo — um ponto de atenção real

Alguns modelos servidos pelo AI Gateway (ex.: modelos "OSS" com raciocínio
interno) devolvem `message.content` como uma **lista de blocos** (um bloco
`reasoning` + um bloco `text`) em vez de uma string simples. Se o app
mostrasse esse conteúdo cru, o raciocínio interno do modelo (que não deveria
aparecer) vazaria para a tela. O app já trata isso: uma função normalizadora
extrai **só** os blocos do tipo `text` antes de exibir a resposta. Se você
trocar de modelo numa instalação nova e a resposta aparecer com lixo/JSON
cru na tela, é sinal de que esse modelo devolve o conteúdo num formato ainda
não coberto — ver [12. Troubleshooting](./12-troubleshooting.md).

## Habilitando numa instalação nova

1. Confirme os pré-requisitos em [03. Pré-requisitos](./03-pre-requisitos.md)
   (AI Gateway disponível + pelo menos um modelo hospedado no workspace).
2. **Crie o model service no Unity Catalog** (é isso que dá o nome de 3
   níveis usado em `LLM_ENDPOINT`):
   - No menu lateral do workspace: **IA/ML → Gateway de IA** → aba
     **Modelos** → **+ Model**.
   - Dê um nome (ex.: `assistente_governanca`) e escolha o catálogo/schema
     onde ele vai ficar registrado — pode ser o mesmo schema de cadastros do
     app (`CADASTRO_CATALOG.CADASTRO_SCHEMA_<env>`) ou outro de sua escolha;
     o nome completo (`catalogo.schema.nome`) é o valor que vai em
     `LLM_ENDPOINT`.
   - Na aba **Roteamento** do model service criado, defina o grupo
     **Principal** — o modelo que será tentado primeiro. Workspaces com
     Unity Catalog geralmente já têm alguns **"Frontier models hosted by
     Databricks"** disponíveis por padrão em `system.ai.*` (pay-per-token,
     sem provisionar nada) — ex.: `system.ai.gpt-oss-120b`,
     `system.ai.llama-4-maverick`. Também é possível apontar para um modelo
     próprio (Model Serving) ou, via a aba **Fornecedores**, configurar um
     provedor externo com API key própria. Dá pra adicionar um modelo de
     **fallback** além do principal.
   - Opcional (aba **Visão geral** → "Configuração de governança"):
     monitoramento de uso, tabela de inferência, limites de taxa e políticas
     (guardrails) — nenhum é obrigatório pro módulo funcionar.
3. **Conceda `EXECUTE`** no model service para o service principal do app —
   aba **Permissões** do model service → **Conceder** → selecione o SP →
   marque `EXECUTE` ("dá a capacidade de usar uma função, modelo ou
   serviço"). Sem isso, o assistente falha ao chamar o endpoint mesmo com
   `LLM_ENABLED=true`.
4. No `app.yaml`, defina `LLM_ENABLED=true` e `LLM_ENDPOINT=<catalogo.schema.nome>`.
5. Redeploy. Teste com uma pergunta simples ("quais domínios estão
   cadastrados?") antes de liberar para os usuários finais.

Sem `LLM_ENABLED=true` **e** `LLM_ENDPOINT` preenchido, o painel mostra
apenas "Assistente de IA não configurado" — o resto do app funciona
normalmente.

> Nota de privacidade: por padrão (`system.ai.*`), o modelo é hospedado pelo
> próprio Databricks dentro do perímetro de dados do workspace — a pergunta e
> o resultado das tools não saem para um provedor externo. Isso só muda se a
> instalação configurar deliberadamente um **provedor externo** (aba
> Fornecedores) como modelo principal ou de fallback — nesse caso, avalie com
> o time de segurança/privacidade do cliente antes de habilitar.
