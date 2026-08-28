# 6. Permissões

Duas camadas independentes: **grants de Unity Catalog** (quem pode ver/tagear
dados de verdade) e **RBAC interno do app** (quem pode editar os cadastros e
o glossário). Uma não substitui a outra.

## O que cada usuário (steward) precisa — módulo Governança de Dados

Para **comentar** uma tabela, o usuário só precisa **enxergá-la** — o
`MODIFY` é emprestado pelo service principal. Para **tags**, como rodam via
OBO, o usuário precisa das permissões de tag do próprio Unity Catalog.
Conceda a cada steward (idealmente a um **grupo**):

1. **`CAN USE`** no SQL Warehouse do ambiente.
2. **Leitura** nos objetos que ele vai documentar:
   ```sql
   GRANT USE CATALOG ON CATALOG <catalogo> TO `<grupo>`;
   GRANT USE SCHEMA, SELECT ON SCHEMA <catalogo>.<schema> TO `<grupo>`;
   ```
   É esse `SELECT`/`USE` que faz a tabela aparecer no `information_schema` do
   usuário — o **portão de acesso** exige isso para liberar o comentário.
3. **Para aplicar/remover tags**, o usuário precisa de `APPLY TAG` no objeto
   e de `ASSIGN` na governed tag:
   ```sql
   GRANT APPLY TAG ON SCHEMA <catalogo>.<schema> TO `<grupo>`;
   ```
   `ASSIGN` é concedido pela UI do Unity Catalog (Governance Hub → Governed
   Tags → Account Permissions → Assign), ao **usuário/grupo** (não ao SP).
   Sem isso, aplicar tag retorna `PERMISSION_DENIED` — comportamento
   esperado. Para só comentar, `APPLY TAG`/`ASSIGN` não são necessários.

> O dropdown de tags no app lista **todas** as governed tags da conta; os
> **objetos** (catálogo/schema/tabela/coluna) aparecem filtrados pelo acesso
> real do usuário.

## O que o service principal do app precisa

### Nos catálogos/schemas de dados que o app vai governar (`ALLOWED_CATALOGS`)

```sql
GRANT USE CATALOG, USE SCHEMA, SELECT, MODIFY ON CATALOG <catalogo> TO `<sp-application-id>`;
```

- `MODIFY` habilita `COMMENT ON TABLE`/`COMMENT ON COLUMN` — a única escrita
  de dados de negócio feita pelo SP.
- `APPLY TAG`/`ASSIGN` **não** são necessários no SP — as tags rodam via OBO.

### No(s) schema(s) internos do app (cadastros + glossário)

```sql
GRANT USE CATALOG ON CATALOG <catalogo_cadastros> TO `<sp-application-id>`;
GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY
  ON SCHEMA <catalogo_cadastros>.<schema_cadastros_env> TO `<sp-application-id>`;
GRANT USE SCHEMA, CREATE TABLE, SELECT, MODIFY
  ON SCHEMA <catalogo_cadastros>.ontologia_<env> TO `<sp-application-id>`;
```

O SP cria as tabelas sozinho (`CREATE TABLE IF NOT EXISTS`) no primeiro
acesso — sem `CREATE TABLE`, o app falha no bootstrap.

### Se o módulo Assistente de IA estiver habilitado

O SP precisa do privilégio **`EXECUTE`** no model service referenciado em
`LLM_ENDPOINT` (concedido no próprio objeto do model service, não num
schema/catálogo) — reaproveita as mesmas leituras do SP acima para as tools,
sem grants adicionais. Onde conceder e como o model service é criado:
[09. Módulo — Assistente de IA](./09-modulo-assistente-ia.md#habilitando-numa-instala%C3%A7%C3%A3o-nova).

### Se `DATABRICKS_ACCOUNT_ID` estiver definido

Ler usuários pela Account SCIM API exige que o SP tenha papel de **Account
admin** na conta (não existe papel granular de "leitor de usuários"). É um
privilégio amplo — avalie com o time de plataforma/segurança do cliente
antes de conceder; se não for aceitável, deixe `DATABRICKS_ACCOUNT_ID` vazio
e use a busca só no workspace local (ou entrada manual de nome/e-mail).

## RBAC interno (Usuários & Permissões, dentro do app)

Controla quem pode **editar** os cadastros, o glossário e aprovar o backlog
de tags — independente das permissões de Unity Catalog:

| Papel | Pode |
|---|---|
| **admin** | CRUD em tudo, gerencia Usuários & Permissões, vê Auditoria, aprova/rejeita o backlog de tags — ignora todas as flags abaixo (enxerga tudo). |
| **editor** | CRUD nos cadastros (domínios/sub-domínios/stewards/dashboards) e no glossário de termos. |
| **leitor** (default para quem não está cadastrado) | Só visualiza. |

Flags por usuário (além do papel), editáveis em Usuários & Permissões:

| Flag | Libera |
|---|---|
| `ver_cadastros` | Grupo **Cadastros** no menu, para não-admin. |
| `ver_logs` | Grupo **Auditoria** (logs de comentário/tag), para não-admin. |
| `aprovador_tags` | Tela de aprovação do backlog de tags de dado pessoal, para não-admin. |
| `power_steward` | **Não libera nada** — é só um rótulo. Marca o usuário como *Power Steward*, fazendo-o aparecer no dropdown **Power Steward** da tela Indicador ([10. Glossário](./10-modulo-glossario-termos.md)). |

O admin inicial é semeado automaticamente a partir de `SEED_ADMIN_EMAIL` na
primeira execução, se a tabela de permissões estiver vazia.

## Quem pode abrir o app

Controle em **Apps → `<nome-do-app>` → Permissions** (`CAN_USE` para quem
vai usar; `CAN_MANAGE` só para mantenedores). Abrir o app **não** concede
acesso a dados — isso continua gated pelos grants de Unity Catalog de cada
um. Editar os cadastros é uma camada adicional, definida pelo RBAC acima.
