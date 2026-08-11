# 7. Guia de uso (usuário de negócio)

## Abrindo o app

Acesse a URL do app no workspace (Databricks → **Apps** →
`governanca-unity-catalog` → **Open**). Você precisa ter permissão `CAN_USE` no app.

No **menu à esquerda** há dois grupos: **Cadastros** (no topo — Domínios,
Sub-domínios, Data Stewards e, para admins, Permissões) e **Governança**
(Governança de Dados — Unity Catalog, a página inicial). Este guia cobre a
Governança; para os cadastros, ver [Cadastros](./10-cadastros.md).

Na **barra lateral** você vê o ambiente (🟡 DEV / 🟢 PRD), o usuário logado, o
warehouse em uso, o seu **perfil de cadastros** (admin/editor/leitor) e um botão
para **atualizar os dados em tela** (limpa o cache de metadados — útil se algo foi
alterado por fora).

## Passo a passo (Governança)

### 1. Selecionar o objeto
No topo, escolha em sequência **Catalog → Schema → Table**.
- O catálogo pode estar restrito (piloto: `suprimentos`).
- Só aparecem os schemas do ambiente do app (em DEV, os terminados em `_dev`) e
  aos quais **você** tem acesso (as listagens rodam com a sua identidade — OBO).

### 2. Comentário da tabela
Logo abaixo do seletor, o bloco **📝 Comentário da tabela** mostra o comentário
atual da tabela. Edite o texto e clique **💾 Salvar comentário da tabela**.
Deixe em branco e salve para **remover** o comentário.

### 3. Analisar as colunas
Ao escolher a tabela, aparece a lista de colunas com **Tipo**, **Comentário** e
**Tags** atuais.
- Use o campo **🔍 Buscar coluna** para filtrar por nome, comentário ou tag
  (filtra a tabela **e** o seletor de coluna do editor).
- Use as checkboxes **"Sem comentário"** e **"Sem Tags"** (combináveis) para
  focar nas lacunas de documentação (colunas sem comentário e/ou sem nenhuma tag).

### 4. Editar uma coluna
Escolha a coluna no seletor **Coluna**. À esquerda:
- **Amostra de dados** — até 5 valores reais, para você entender o conteúdo;
- **Tags atuais** da coluna (se houver).

À direita, três blocos:
- **📝 Comentário** — edite o texto. Deixe em branco e salve para **remover** o
  comentário.
- **🏷️ Adicionar / atualizar tag governada** — **dois campos lado a lado**:
  **Chave da tag** e **Valor da tag**. Ao escolher a chave, o campo de valor é
  habilitado (dropdown quando a tag tem valores fixos; texto livre caso
  contrário). Ambos são necessários para aplicar.
- **🗑️ Remover tags desta coluna** — selecione uma ou mais tags aplicadas para
  remover.

### 5. Salvar
Clique **💾 Salvar e Aplicar Governança**. O app executa, na ordem: comentário →
remoções → nova tag, e mostra ✅/❌ **por comando**.

> As escritas (comentário de tabela/coluna e tags) são feitas pelo **service
> principal** do app — você **não precisa de `MODIFY`**. Você só consegue salvar
> em tabelas às quais **tem acesso** (o app confere isso com a sua identidade
> antes de gravar).

## Observações importantes

- **Só tags governadas** aparecem para seleção — é o catálogo oficial da empresa.
- Se aparecer erro `PERMISSION_DENIED ... tag assignment`, falta a permissão
  **ASSIGN** na tag **para o service principal do app** (peça ao time de
  governança) — ver [Troubleshooting](./09-troubleshooting.md).
- Se aparecer "**Você não tem acesso a esta tabela**", o app não conseguiu
  confirmar (com a sua identidade) que você enxerga a tabela — peça `SELECT`
  no schema.
- **Em DEV, o que você salvar na Governança é temporário**: o espelhamento de
  domingo (PROD→DEV) sobrescreve. Documentação definitiva deve ser feita no app de
  PROD. (Os **cadastros** não são afetados pelo sync.) Ver
  [Ambientes](./05-ambientes-e-sync.md).
