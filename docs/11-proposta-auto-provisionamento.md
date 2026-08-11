# 11. Proposta — Auto-provisionamento de acesso ao workspace pelo app

> ⚠️ **STATUS: PROPOSTA — NÃO IMPLEMENTADO.** Registrado em 2026-08-05 para
> validação futura. Antes de acionar, validar o desenho junto às tabelas
> existentes do Unity Catalog (cadastros em `apps.governanca_unity_catalog_<env>`).

## Objetivo

Hoje, adicionar alguém em **Usuários & Permissões** (ou como **Data Steward**)
grava apenas o registro nas tabelas Delta do app — não concede nenhum acesso.
Se o usuário não estiver provisionado no workspace onde o app roda, ele não
consegue **abrir** o app daquele ambiente (ver [Permissões](./04-permissoes.md),
seção Account SCIM).

A proposta: ao adicionar um usuário no app, **também liberar o acesso dele ao
workspace do ambiente** — app de DEV → workspace de DEV, app de PROD → workspace
de PROD; se já estiver provisionado, apenas gravar o cadastro (operação
idempotente). Equivalente automatizado do fluxo manual *Workspace settings →
Identity and access → Add user*.

## Desenho recomendado: provisionar via grupo de conta (não usuário a usuário)

Criar **uma única vez** (setup manual no Account console, fora do app) um grupo
de conta `governanca-stewards` já configurado com o acesso mínimo da "Opção A":

- **Atribuído aos workspaces** de DEV e PROD;
- Entitlement **Databricks SQL access** apenas (sem *Workspace access*, sem
  *cluster creation*) — o usuário consegue usar o app e o warehouse, mas não
  navega em notebooks/jobs/clusters;
- **`CAN_USE` no app** e **`CAN_USE` no warehouse** de cada ambiente.

Com isso o app precisa de **uma só operação idempotente** ao adicionar um
usuário: verificar se o e-mail é membro do grupo e, se não for, adicioná-lo via
Account API (`AccountClient.groups.patch(...)`). Todo o resto (assignment ao
workspace, entitlements, warehouse, app) vem por **herança do grupo**.

**Por que não usuário a usuário:** o caminho alternativo (workspace assignment +
entitlements SCIM + permissões de app/warehouse por usuário) são 3+ chamadas por
pessoa, fáceis de divergir com o tempo e mais difíceis de auditar. Grupo é
configuração declarada uma vez e auditável num único lugar.

## Permissões necessárias do SP

- O papel **Account admin** já concedido ao SP de PROD (`app-5o47oi`, para a
  busca Account SCIM) é suficiente para gerenciar membership de grupo de conta —
  **não** é preciso workspace admin.
- O SP de **DEV** (`app-446ya1`) **não tem** Account admin. Decidir: conceder
  também, ou ativar o auto-provisionamento **só em PROD** (flag por ambiente,
  DEV continua manual).

## Salvaguardas (a incluir na implementação)

1. **Ação restrita a `admin`** do RBAC do app (tabela `permissoes`).
2. **Checkbox explícito** "Provisionar acesso ao workspace" no formulário — nunca
   automático/silencioso.
3. **Log de auditoria** da concessão (mesmo padrão de `log_comentarios`/`log_tags`,
   ex.: tabela `log_provisionamento`: quem concedeu, para quem, grupo, quando).
4. **Exclusão simétrica**: remover o usuário do app remove do grupo
   `governanca-stewards` — mas **nunca** remove o usuário do workspace/conta.

## Pontos em aberto (validar antes de implementar)

- **Desenho junto às tabelas do UC/cadastros**: como o vínculo
  `permissoes`/`data_stewards` ↔ membership do grupo fica consistente (ex.:
  coluna `provisionado_em`/`provisionado_por`? reconciliação periódica?).
- **Governança de identidade**: alinhar se todo acesso deve nascer no **Entra ID**
  (grupo sincronizado via SCIM do IdP) em vez de grupo nativo Databricks gerido
  pelo app. Membership de grupo nativo não conflita com o SCIM sync do IdP, mas
  centralizar no Entra ID pode ser preferência do time de plataforma.
- **Escalação de poder do app**: hoje o SP só **lê** usuários da conta; com a
  proposta ele passa a **conceder acesso**. Confirmar aceite do time de
  plataforma/segurança.
