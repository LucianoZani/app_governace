# 12. Troubleshooting

## Autenticação / OBO

### `more than one authorization method configured: oauth and pat`
O SDK detectou as duas credenciais (OAuth do service principal injetado no
ambiente **e** o token do usuário). O cliente OBO deve ser criado com
`auth_type="pat"`, que força o uso **apenas** do token do usuário. Se
reaparecer numa customização, confirme que essa opção não foi removida.

### `Provided OAuth token does not have required scopes: sql`
O app não tem o scope `sql` habilitado. Adicione o scope e refaça
restart/redeploy:
```bash
databricks apps update <nome-do-app> --json '{"user_api_scopes":["sql"]}'
```
No primeiro acesso após habilitar, cada usuário **consente** com o scope —
se a tela de consentimento não aparecer, abra o app numa janela anônima.

## Governança de Dados

### `PERMISSION_DENIED ... not authorized to update the tag assignment for the following tag policies`
Falta `ASSIGN` na governed tag **para o usuário/grupo** (as tags rodam via
OBO). Conceda no Governance Hub (Governed Tags → Account Permissions →
Assign). Propaga em ~30s.

### `PERMISSION_DENIED` do warehouse
Falta `CAN USE` no SQL Warehouse do ambiente para o **usuário**. Verifique
também se o warehouse está **RUNNING**.

### O catálogo/schema que eu quero não aparece
- Catálogo fora de `ALLOWED_CATALOGS`.
- Schema fora do filtro de `ENVIRONMENT` do app.
- Usuário sem `USE CATALOG`/`USE SCHEMA` para enxergar (as listagens são OBO).

### `PERMISSION_DENIED` ao aplicar comentário
Falta `MODIFY` (ou ownership) no objeto **para o service principal**.
Conceda `MODIFY` ao SP no schema alvo — o usuário não precisa de `MODIFY`.

### "Você não tem acesso a esta tabela — alteração bloqueada"
O portão de acesso (OBO) não confirmou que o usuário enxerga a tabela. Ele
precisa de `USE CATALOG` + `USE SCHEMA` + `SELECT` no schema. Confirme
também que o scope `sql` está habilitado — sem token do usuário, o portão
não consegue avaliar o acesso.

### Uma tag em coluna de dado pessoal "some" / não aplica e mostra "foi para aprovação"
Comportamento esperado da regra de compliance (ver
[07. Módulo — Governança de Dados](./07-modulo-governanca-dados.md)): a
tentativa foi desviada para o **backlog de aprovação** em vez de aplicada
direto. Resolva na tela Cadastros → Backlog de Aprovação de Tags.

## Cadastros e Administração

### Não consigo editar/adicionar cadastros (só vejo os dados)
Perfil é `leitor` (padrão para quem não está em Usuários & Permissões). Peça
a um admin para promover a `editor`/`admin`.

### "Não é possível excluir: há registros vinculados"
Exclusão bloqueada por dependência (domínio com sub-domínios/stewards;
sub-domínio com stewards). Remova os vínculos primeiro.

### "Não é possível remover o último admin"
Sempre deve existir ao menos um `admin`. Promova outro usuário antes de
remover o atual.

### "Cadastros indisponíveis: …" na barra lateral
Falha no bootstrap (`ensure_cadastro_tables`). Geralmente falta ao SP
`USE CATALOG`/`USE SCHEMA`/`CREATE TABLE` no schema de cadastros do
ambiente. Conceda os grants (ver [06. Permissões](./06-permissoes.md)).

## Glossário de Termos de Negócio

### Erro ao salvar/consultar um termo, mas os cadastros normais funcionam
O SP pode ter grants no schema de **cadastros** mas não no schema do
**glossário** (`ontologia_<env>`) — são schemas diferentes dentro do mesmo
catálogo. Confirme os grants separadamente para os dois.

## Assistente de Governança (IA)

### "Assistente de IA não configurado"
`LLM_ENABLED` e/ou `LLM_ENDPOINT` não estão definidos no `app.yaml`. Esse é
o estado padrão/esperado se o módulo não foi habilitado — não é erro.

### "Não consegui me conectar ao assistente de IA: …"
Falha ao obter token do SP ou ao montar o cliente contra o AI Gateway.
Confirme que o workspace tem AI Gateway habilitado e que o host resolvido
está correto (`{host}/ai-gateway/mlflow/v1`).

### "O assistente de IA falhou ao responder: …"
Geralmente `LLM_ENDPOINT` aponta para um model service que não existe, foi
renomeado, ou o SP não tem permissão para invocá-lo. Confirme o nome
completo (`catalogo.schema.nome`) e o grant de uso do endpoint.

### A resposta do assistente aparece com texto estranho/JSON cru na tela
Sinal de que o modelo configurado devolve `message.content` num formato
diferente do esperado (a normalização atual só trata string simples e lista
de blocos com um bloco `type=="text"`). Veja a nota de compatibilidade em
[09. Módulo — Assistente de IA](./09-modulo-assistente-ia.md) — pode ser
necessário ajustar a função de extração de texto para o formato desse
modelo específico.

### O assistente "não sabe" algo que já está cadastrado no app
Confira se a informação está numa fonte que tem uma **tool** dedicada (ver
a lista em [09. Módulo — Assistente de IA](./09-modulo-assistente-ia.md)).
Se não tiver, o modelo não consegue consultar aquele dado — é preciso
adicionar uma tool nova em código.

## Deploy

### `databricks sync` diz "Complete" mas nada sobe
O snapshot pode achar que não há mudanças. Use `--full` para forçar o
upload.

### `Error downloading source code / no files found` (no `apps deploy`)
O `--source-code-path` aponta para uma pasta vazia ou sem permissão para o
SP do app. Confirme que o sync subiu os arquivos (`--full`) e que o SP tem
leitura na pasta.

### O app abre mas `DATABRICKS_WAREHOUSE_ID` não definido
Defina a env var no `app.yaml`. Sem ela o app para com erro explícito na
inicialização.

### Fiz upload manual pela Workspace UI e o arquivo não atualizou
A importação **não sobrescreve** um arquivo existente — cria um novo com
timestamp no nome. Mova o arquivo antigo para a Lixeira e renomeie o novo
para o nome original antes de clicar em **Implementar**.
