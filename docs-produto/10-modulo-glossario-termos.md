# 10. Módulo — Glossário de Termos de Negócio

Cadastro rico de **Termos** de negócio e **Indicadores** (KPIs) num único
tipo de registro, para dar a analistas e áreas de negócio uma fonte única de
verdade sobre o que cada termo/métrica significa, quem é dono dela e como é
calculada.

## Duas telas: edição e consulta

| Tela | Menu | Para quê |
|---|---|---|
| **Termos de Negócio (edição)** | Cadastros | criar/editar/excluir; exige papel `admin`/`editor` |
| **Termos de Negócio** (consulta) | Glossário | busca aberta e card de detalhe somente leitura; sempre visível |

## Termo vs. Indicador — formulário condicional

Ao criar um registro, o campo **Tipo** (no topo, fora do form, reativo)
define se é um "Termo" comum ou um "Indicador". Escolher **Indicador**
revela os campos exclusivos: unidade, nível de apuração, variáveis
utilizadas, restrições, o picker de tabelas/colunas da **dimensão** e da
**métrica**, e a memória de cálculo. Para um "Termo" comum esses campos não
aparecem e são gravados vazios (`""` / `"[]"`).

### Picker de tabelas e colunas (dimensão / métrica)

Para indicadores, "Dimensão" e "Métrica" são montadas com o mesmo seletor
encadeado Catalog → Schema → Table do módulo de Governança. Cada tabela
adicionada pode trazer uma ou mais colunas (nenhuma = tabela inteira);
várias tabelas podem compor cada um dos dois. É gravado como lista JSON nas
colunas `dimensao_tabelas` / `metrica_tabelas`.

> Colunas legadas `fonte_variavel`, `tipo_grafico` e `dimensoes` (versão
> anterior do módulo) continuam na tabela por compatibilidade com registros
> antigos, mas a tela não lê nem grava mais nelas.

## Campos do registro

| Campo | Descrição |
|---|---|
| `tipo` | `Termo` ou `Indicador`. |
| `nome` | Nome do termo/indicador. |
| `objetivo` | Para que serve / o que representa. |
| `observacoes` | Notas livres. |
| `palavras_chave` | Sinônimos/termos relacionados, para facilitar busca. |
| `macroprocesso` | Processo de negócio ao qual pertence. |
| `dominio_id` / `subdominio_id` | Domínio e sub-domínio de dados (reaproveita os cadastros de [08. Cadastros](./08-modulo-cadastros.md)). |
| `data_owner` | Dropdown filtrado pelos **Data Owners** (tipo `Owner`) cadastrados para o domínio/sub-domínio escolhido; sem domínio ou sem ninguém cadastrado, vira texto livre. |
| `data_steward` | Idem, filtrado pelos **Data Stewards** (tipo `Steward`) daquele domínio/sub-domínio. |
| `rotulo_seguranca` / `rotulo_privacidade` | Dropdowns alimentados pelo mesmo catálogo de **governed tags** (`seguranca`/`privacidade`) usado na Governança de Dados — mantém a classificação do termo consistente com a das colunas. |
| `nivel_apuracao` / `unidade` | *Só indicador*: granularidade (diário, mensal, …) e unidade de medida. `unidade` é um dropdown com valores comuns (R$, %, un, …) mais "Outra…" para texto livre. |
| `variaveis_utilizadas` / `memoria_calculo` | *Só indicador*: quais variáveis entram na conta e a fórmula/memória de cálculo. |
| `dimensao_tabelas` / `metrica_tabelas` | *Só indicador*: lista JSON de `{catalogo, schema, tabela, colunas[]}` montada no picker de tabelas/colunas. |
| `restricoes` | *Só indicador*: restrições de uso/acesso relevantes. |

## Onde fica armazenado

Schema **dedicado**, separado do schema de cadastros internos — por padrão
`<CADASTRO_CATALOG>.ontologia_<ENVIRONMENT>` (ex.: `apps.ontologia_prd`).
Ver a nota de configuração em [05. Configuração](./05-configuracao.md): esse
nome de schema **não** tem variável de ambiente própria hoje — está fixo no
código como `"ontologia_" + ENVIRONMENT`, dentro do catálogo de
`CADASTRO_CATALOG`.

## RBAC

A tela de **edição** segue o RBAC dos Cadastros
([06. Permissões](./06-permissoes.md)): `admin`/`editor` criam/editam/
excluem; `leitor` só visualiza; a tela aparece pra quem tem `ver_cadastros`.
A tela de **consulta** (grupo Glossário) é sempre visível e é só leitura.

## Integração com o Assistente de IA

Se o módulo de [Assistente de Governança (IA)](./09-modulo-assistente-ia.md)
estiver habilitado, o glossário é uma das fontes de consulta (`tools`)
disponíveis ao modelo — perguntas como "o que é o indicador X?" ou "quem é o
data owner do termo Y?" podem ser respondidas diretamente pelo chat, sem
abrir a tela do glossário.
