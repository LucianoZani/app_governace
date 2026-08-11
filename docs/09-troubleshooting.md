# 9. Troubleshooting

## Autenticação / OBO

### `more than one authorization method configured: oauth and pat`
O SDK detectou **as duas** credenciais (o OAuth do service principal injetado no
ambiente **e** o token do usuário). **Já resolvido no código**: o cliente OBO é
criado com `auth_type="pat"`, que força o uso **apenas** do token do usuário. Se
reaparecer, confirme que `get_client(prefer_user=True)` está usando `auth_type="pat"`.

### `Provided OAuth token does not have required scopes: sql`
O app (ou o usuário) não tem o scope `sql`. Adicione o scope ao app e refaça
restart/redeploy:
```bash
databricks apps update governanca-unity-catalog --json '{"user_api_scopes":["sql"]}'
```
No primeiro acesso, **cada usuário consente** com o scope — se a tela de
consentimento não aparecer, abra o app numa **janela anônima**. Ver
[Permissões](./04-permissoes.md).

## Uso do app (Governança)

### `PERMISSION_DENIED: ... not authorized to update the tag assignment for the following tag policies: <tag>`
Falta a permissão **`ASSIGN`** na governed tag **para o service principal do app**
(as escritas rodam pelo SP). Conceda no Governance Hub (Catalog → Govern → Governed
Tags → **Account Permissions** → **Assign** ao SP). Ver
[Permissões](./04-permissoes.md). Propaga em ~30s.

### `PERMISSION_DENIED` do warehouse (ao rodar qualquer query)
Falta `CAN USE` no **SQL Warehouse** do ambiente para o **usuário** (as queries
rodam como ele). Conceda o `CAN USE` e verifique se o warehouse está **RUNNING**.

### O catálogo/schema que eu quero não aparece
- **Catálogo:** pode estar fora de `ALLOWED_CATALOGS` (atual = `suprimentos,rh`).
- **Schema:** o filtro de ambiente esconde schemas do outro ambiente
  (em DEV só `*_dev`). Confira `ENVIRONMENT`.
- **Permissão:** o **usuário** precisa de `USE CATALOG` + `USE SCHEMA` para enxergar
  (as listagens são OBO).

### O catálogo não aparece no Catalog Explorer (após criá-lo)
A UI do Catalog Explorer pode estar com cache. Faça **refresh** da página/UI.

### `PERMISSION_DENIED` ao aplicar comentário
Falta `MODIFY` (ou ownership) no objeto **para o service principal do app** (as
escritas rodam pelo SP). Conceda `MODIFY` ao SP no schema alvo — ver
[Permissões](./04-permissoes.md). O **usuário** não precisa de `MODIFY`.

### "Você não tem acesso a esta tabela — alteração bloqueada"
O **portão de acesso** (`user_can_access_table`, OBO) não confirmou que o usuário
logado enxerga a tabela. O usuário precisa de `USE CATALOG` + `USE SCHEMA` +
`SELECT` no schema (o que o faz aparecer no `information_schema` dele). Confirme
também que o **OBO está habilitado** (scope `sql`) — sem token do usuário o portão
não consegue avaliar o acesso.

### A amostra de dados não carrega
O **usuário** precisa de `SELECT` no schema/tabela e `CAN USE` no warehouse.
Verifique também se o warehouse está **RUNNING**.

### Salvei uma tag/comentário em DEV e sumiu
Comportamento esperado: o job `sync_prod_to_dev` (domingo) re-espelha PROD→DEV e
sobrescreve a governança de dados. Faça a governança durável em **PROD**. (Os
**cadastros** não são afetados.) Ver [Ambientes](./05-ambientes-e-sync.md).

### O valor da tag não é aceito
Governed tags exigem valor da lista permitida da Tag Policy. O app já restringe
via dropdown quando há valores definidos; se for texto livre, confira a policy.

## Uso do app (Cadastros)

### Não consigo editar/adicionar cadastros (só vejo os dados)
Seu **perfil é `leitor`** (padrão para quem não está em `permissoes`). Peça a um
**admin** para lhe dar `editor` ou `admin` na página **Permissões**. Ver
[Cadastros](./10-cadastros.md).

### "Não é possível excluir: há registros vinculados"
Exclusão é bloqueada quando há dependências (domínio com sub-domínios/stewards;
sub-domínio com stewards). Remova os vínculos primeiro.

### "Não é possível remover o último admin"
Sempre deve existir ao menos um `admin` em `permissoes`. Promova outro usuário a
admin antes de remover.

### "Cadastros indisponíveis: …" na barra lateral
Falha no bootstrap (`ensure_cadastro_tables`). Geralmente o **SP** não tem
`USE CATALOG`/`USE SCHEMA`/`CREATE TABLE` no schema de cadastros do ambiente
(`apps.governanca_unity_catalog_<env>`). Conceda os grants (ver
[Permissões](./04-permissoes.md)).

## Deploy

### `databricks sync` diz "Complete" mas nada sobe
O snapshot pode achar que não há mudanças. Use `--full` para forçar o upload.

### `Error downloading source code / no files found` (no `apps deploy`)
O `--source-code-path` aponta para uma pasta vazia ou sem permissão para o SP do
app. Confirme que o `databricks sync` subiu os arquivos (use `--full`) e que o SP
tem leitura na pasta.

### O app abre mas `DATABRICKS_WAREHOUSE_ID` não definido
Defina a env var no `app.yaml` (bloco `env`). Sem ela o app para com erro
explícito na inicialização. Ajuste o warehouse conforme o ambiente antes de subir.
