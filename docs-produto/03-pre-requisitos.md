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
| **Unity AI Gateway disponível** no workspace | Nem toda edição/plano de Databricks tem o AI Gateway habilitado. Confirme em **Serving** → **AI Gateway** (ou com o time de plataforma) antes de prometer esse módulo ao cliente. |
| **Ao menos um modelo hospedado** acessível via AI Gateway (endpoint tipo *model serving*/*foundation model*) | O app não traz modelo nenhum embutido — ele aponta para um model service já existente no workspace (`catalogo.schema.nome_do_modelo`). Se o workspace não tiver nenhum modelo hospedado nem acesso a *foundation models* do provedor, este módulo específico não vai funcionar (o resto do app funciona normalmente). |
| **Permissão do service principal para invocar o model service** | Grant de uso do endpoint de serving para o SP do app (ver [06. Permissões](./06-permissoes.md)). |

> Sem AI Gateway/modelo disponível, **não é bloqueante para o resto do app**:
> deixe `LLM_ENABLED=false` e os módulos de Governança, Cadastros e Glossário
> funcionam normalmente — o painel do Assistente mostra uma mensagem de "não
> configurado" em vez de dar erro.

## Recomendado, mas opcional

| Item | Por quê |
|---|---|
| `DATABRICKS_ACCOUNT_ID` + permissão de leitura de usuários na conta (Account SCIM) | Amplia a busca de usuário (steward/permissão) para gente que existe na conta mas ainda não foi provisionada nesse workspace específico. Sem isso, o app cai para busca só no workspace local, ou entrada manual de nome/e-mail. |
| CLI do Databricks autenticada na máquina de quem for instalar | Agiliza o deploy (ver [04. Instalação](./04-instalacao.md)). Sem ela, o deploy é feito manualmente pela Workspace UI — mais lento, mas funciona. |
