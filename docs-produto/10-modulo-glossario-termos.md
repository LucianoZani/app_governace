# 10. Módulo — Glossário de Negócio e Indicadores

Cadastro rico de **Termos** de negócio e de **Indicadores** (KPIs), para dar a
analistas e áreas de negócio uma fonte única de verdade sobre o que cada termo/
métrica significa, quem é dono dela e como é calculada.

## Três telas: duas de edição + uma de consulta

| Tela | Menu | Para quê |
|---|---|---|
| **Glossário de Negócio** | Cadastros | criar/editar/excluir **termos**; exige papel `admin`/`editor` |
| **Indicador** | Cadastros | criar/editar/excluir **indicadores** (KPIs); exige papel `admin`/`editor` |
| **Termos de Negócio** (consulta) | Glossário | busca aberta e card de detalhe somente leitura sobre termos **e** indicadores; sempre visível |

As duas telas de edição compartilham o mesmo formulário base (nome,
domínio/sub-domínio, data owner/steward, macroprocesso, palavras-chave e a
definição/objetivo). A tela **Indicador** acrescenta a classificação de
segurança/privacidade, observações e os campos exclusivos de KPI.

### Formulário de Glossário de Negócio

Enxuto: **Nome do termo**, **Macroprocesso**, **Palavras-chave**, **Definição**,
mais Domínio / Sub-domínio / Data Owner / Data Steward. Não tem classificação
de segurança/privacidade nem observações (isso é só de Indicador). O campo
antes chamado "Objetivo" aparece aqui como **Definição** (a coluna no banco
continua `objetivo`).

### Palavras-chave em "chips"

Nas duas telas, o campo **Palavras-chave** funciona por adição: digite uma
palavra e tecle **Enter** — ela vira um chip abaixo do campo e o campo limpa
para a próxima. Clique no `✕` de um chip para removê-lo. É gravado na coluna
`palavras_chave` como texto separado por vírgula (compatível com registros
antigos).

## Campos exclusivos de Indicador

- **Power Steward** (primeiro campo do formulário) — dropdown opcional com os
  usuários marcados com a flag `power_steward` em
  [Usuários & Permissões](./06-permissoes.md). Mostra o nome, grava o e-mail
  na coluna `power_steward` de `indicadores`. Se ninguém estiver marcado, o
  campo fica vazio e mostra um aviso apontando para a tela de permissões.
- **Classificação** — Rótulo de segurança / privacidade (mesmos catálogos de
  governed tags usados na Governança de Dados).
- **Observações** — notas livres.
- **Unidade** — dropdown com valores comuns (R$, %, un, …) mais "Outra…" para
  texto livre.
- **Nível de apuração** — granularidade (diário, mensal, …).
- **Variáveis utilizadas** e **Memória de cálculo (fórmula)**.
- **Restrições** de uso/acesso.
- **Dimensão** e **Métrica** — montadas com o mesmo seletor encadeado
  Catalog → Schema → Table do módulo de Governança. Cada tabela adicionada
  pode trazer uma ou mais colunas (nenhuma = tabela inteira); várias tabelas
  podem compor cada um dos dois. Gravadas como lista JSON nas colunas
  `dimensao_tabelas` / `metrica_tabelas`.

## Onde fica armazenado

Schema **dedicado**, separado do schema de cadastros internos — por padrão
`<CADASTRO_CATALOG>.ontologia_<ENVIRONMENT>` (ex.: `apps.ontologia_prd`). Ver
a nota de configuração em [05. Configuração](./05-configuracao.md): esse nome
de schema **não** tem variável de ambiente própria hoje — está fixo no código
como `"ontologia_" + ENVIRONMENT`, dentro do catálogo de `CADASTRO_CATALOG`.

Duas tabelas:

| Tabela | Conteúdo |
|---|---|
| `glossario_negocio` | Termos de negócio (campos base). |
| `indicadores` | Indicadores — campos base **mais** `nivel_apuracao`, `unidade`, `variaveis_utilizadas`, `memoria_calculo`, `restricoes`, `dimensao_tabelas`, `metrica_tabelas`. |

Ambas guardam uma coluna `tipo` (`'Termo'` / `'Indicador'`, valor fixo por
tabela) — usada pela tela de consulta e pelo card de detalhe, que tratam os
dois num só lugar.

> **Migração automática (uma vez).** Instalações anteriores tinham uma única
> tabela `termos_negocio` com um seletor de tipo. No primeiro boot da versão
> nova, o app copia cada registro para `glossario_negocio` ou `indicadores`
> conforme o `tipo` e então dropa `termos_negocio`. É idempotente: se algum
> `INSERT` falhar, o `DROP` não roda e o próximo boot retoma. As colunas
> legadas `fonte_variavel` / `tipo_grafico` / `dimensoes` (versões antigas do
> módulo) não são migradas.

## RBAC

As telas de **edição** seguem o RBAC dos Cadastros
([06. Permissões](./06-permissoes.md)): `admin`/`editor` criam/editam/excluem;
`leitor` só visualiza; aparecem para quem tem `ver_cadastros`. A tela de
**consulta** (grupo Glossário) é sempre visível e é só leitura.

## Integração com o Assistente de IA

Se o módulo de [Assistente de Governança (IA)](./09-modulo-assistente-ia.md)
estiver habilitado, o glossário é uma das fontes de consulta (`tools`)
disponíveis ao modelo — a ferramenta `termos_de_negocio` devolve as duas
tabelas juntas, então perguntas como "o que é o indicador X?" ou "quem é o
data owner do termo Y?" podem ser respondidas direto pelo chat.
