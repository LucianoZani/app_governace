# 3. Pré-requisitos

Confirme tudo isto **antes** de começar a instalação ([04. Instalação](./04-instalacao.md)).

## Sempre necessários

| Requisito | Por quê |
|---|---|
| **Unity Catalog habilitado** no workspace de destino | O app não funciona em modo *hive_metastore* legado — tags governadas, `information_schema` e Tag Policies são recursos de Unity Catalog. |
| **Um SQL Warehouse** (qualquer tamanho, DBSQL) | Todo SQL do app (navegação, leitura de metadados, DDL de tags/comentário, cadastros) roda contra um warehouse via Statement Execution API. |
| **Permissão para criar Databricks Apps** no workspace | O produto é distribuído como Databricks App. Se você só tem acesso de "usuário comum", peça essa permissão ao admin do workspace antes de seguir. |
| **Um service principal** (criado junto com o app, ou um existente dedicado) | Executa comentários, todos os cadastros, o glossário, os logs de auditoria e (se habilitado) o Assistente de IA. |
| **Pelo menos um catálogo de dados** já existente, com tabelas para documentar | O app não cria catálogos de dados de negócio — só o(s) catálogo(s)/schema(s) internos dele mesmo (cadastros e glossário). |
| **Um catálogo oficial de Governed Tags / Tag Policies** configurado no Unity Catalog (Governance Hub) | O app só **consome** esse catálogo — ele não cria chaves/valores de tag. Se o workspace ainda não tem nenhuma governed tag definida, defina pelo menos uma antes de testar o módulo de Governança. |

## Só se for habilitar o Assistente de Governança (IA) — opcional

| Requisito | Por quê |
|---|---|
| **Unity AI Gateway disponível** no workspace | Nem toda edição/plano de Databricks tem o AI Gateway habilitado. Confirme em **IA/ML → Gateway de IA** (ou com o time de plataforma) antes de prometer esse módulo ao cliente. |
| **Um model service criado no Unity Catalog** (`catalogo.schema.nome`) apontando pra um modelo | O app não traz modelo nenhum embutido — ele aponta para um model service registrado no UC. Criado em **IA/ML → Gateway de IA → Modelos → + Model**; na aba **Roteamento** do model service, aponte pra um dos **"Frontier models hosted by Databricks"** já disponíveis por padrão (`system.ai.*`, pay-per-token — ex.: `system.ai.gpt-oss-120b`), pra um endpoint de Model Serving próprio, ou (aba **Fornecedores**) pra um provedor externo com API key. Passo detalhado em [09. Módulo — Assistente de IA](./09-modulo-assistente-ia.md#habilitando-numa-instala%C3%A7%C3%A3o-nova). |
| **Grant `EXECUTE` do service principal no model service** | Sem `EXECUTE` no objeto do model service (aba **Permissões** dele → Conceder), o assistente falha ao chamar o endpoint mesmo com `LLM_ENABLED=true` (ver [06. Permissões](./06-permissoes.md)). |

> Sem AI Gateway/modelo disponível, **não é bloqueante para o resto do app**:
> deixe `LLM_ENABLED=false` e os módulos de Governança, Cadastros e Glossário
> funcionam normalmente — o painel do Assistente mostra uma mensagem de "não
> configurado" em vez de dar erro.

## Recomendado, mas opcional

| Item | Por quê |
|---|---|
| `DATABRICKS_ACCOUNT_ID` + permissão de leitura de usuários na conta (Account SCIM) | Amplia a busca de usuário (steward/permissão) para gente que existe na conta mas ainda não foi provisionada nesse workspace específico. Sem isso, o app cai para busca só no workspace local, ou entrada manual de nome/e-mail. |
| CLI do Databricks autenticada na máquina de quem for instalar | Agiliza o deploy (ver [04. Instalação](./04-instalacao.md)). Sem ela, o deploy é feito manualmente pela Workspace UI — mais lento, mas funciona. |
