# 7. Módulo — Governança de Dados

Página inicial do app. Usuários de negócio documentam tabelas/colunas do
Unity Catalog sem precisar de acesso de escrita aos dados.

## Funcionalidades

1. **Navegação encadeada** Catalog → Schema → Table.
   - O catálogo pode ser restrito por `ALLOWED_CATALOGS`.
   - Os schemas mostrados dependem do `ENVIRONMENT` do app (filtro por
     convenção de sufixo, ex. `_dev`) e do acesso do usuário logado.
2. **Comentário da tabela** — adiciona/edita/remove (`COMMENT ON TABLE`;
   salvar em branco remove).
3. **Listagem de colunas** com tipo, comentário atual e tags atuais.
4. **Filtros** "Sem comentário" e "Sem Tags" (combináveis) para achar lacunas
   de documentação, e busca por nome/comentário/tag.
5. **Amostra de dados** — até 5 linhas da coluna selecionada, para dar
   contexto antes de documentar.
6. **Somente tags governadas** — o dropdown lista apenas as chaves/valores
   permitidos pelo catálogo oficial de Governed Tags/Tag Policies do Unity
   Catalog (o app não inventa tags).
7. **Editor por coluna**, aplicado num único "Salvar e Aplicar Governança":
   comentário, adicionar/atualizar uma tag, remover tags já aplicadas.
8. **Portão de acesso** — só é possível documentar tabelas que o usuário
   logado enxerga (validado via OBO antes de cada escrita de comentário).

## Modelo de autenticação (resumo — detalhes em [02. Arquitetura](./02-arquitetura.md))

- **Leituras e tags → identidade do usuário (OBO).**
- **Comentário → service principal**, atrás do portão de acesso.

## Regra de compliance de tagueamento de dado pessoal

Algumas colunas guardam dado pessoal (CPF, e-mail, etc.). O app impede que
essas colunas sejam marcadas incorretamente:

1. Uma coluna é considerada **dado pessoal** se o nome dela contém algum dos
   **padrões cadastrados** em Cadastros → Padrões de Dado Pessoal (substring,
   case-insensitive — ex.: padrão `cpf` casa com `numero_cpf`, `cpf_cliente`).
2. Para colunas de dado pessoal, as chaves de tag governadas configuradas em
   `TAG_COMPLIANCE_RULES` (por padrão: `privacidade` e `seguranca`) só podem
   ter os valores exigidos (por padrão: `dado pessoal` e `confidencial`,
   respectivamente — ver [05. Configuração](./05-configuracao.md)).
3. Uma tentativa de gravar essas chaves com **outro valor**, ou de
   **remover** uma delas numa coluna de dado pessoal, **não é aplicada
   direto no Unity Catalog** — o app grava a tentativa no **backlog de
   aprovação de tags** (com motivo, solicitante e valores antes/depois) e
   mostra "⏳ foi para aprovação" ao usuário, em vez de aplicar.
4. Um **aprovador** (admin ou usuário com a flag `aprovador_tags`) decide o
   item pendente na tela de Cadastros → Backlog de Aprovação — ver
   [08. Módulo — Cadastros e Administração](./08-modulo-cadastros.md).

> Outras chaves de tag (fora de `TAG_COMPLIANCE_RULES`) nunca passam pelo
> backlog — são aplicadas normalmente.

## O que o módulo NÃO faz

- Não cria/edita as governed tags em si.
- Não aplica tags em várias colunas de uma vez (limitação do Unity Catalog:
  `SET TAGS` não aceita múltiplas colunas no mesmo `ALTER TABLE`; o app
  aplica coluna a coluna).
- Não altera dados — apenas metadados (tags e comentários).

## Notas técnicas do Unity Catalog

- Chaves de tag são *case-sensitive*; máximo de 50 tags por objeto.
- `SET TAGS`/`UNSET TAGS` não aceitam parâmetros — os valores são quotados
  com escaping seguro diretamente no SQL montado pelo app.
- Tags governadas com valores fixos exigem que o valor esteja na lista
  permitida da Tag Policy — o app já restringe via dropdown quando a policy
  define valores.
