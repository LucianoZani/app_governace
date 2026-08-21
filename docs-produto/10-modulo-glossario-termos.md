# 10. Módulo — Glossário de Termos de Negócio

Cadastro rico de **Termos** de negócio e **Indicadores** (KPIs) num único
tipo de registro, para dar a analistas e áreas de negócio uma fonte única de
verdade sobre o que cada termo/métrica significa, quem é dono dela e como é
calculada.

## Termo vs. Indicador — um único formulário

Ao criar um registro, o campo **Tipo** define se é um "Termo" comum ou um
"Indicador". Os campos **exclusivos de indicador** (variáveis utilizadas,
fonte da variável, memória de cálculo, tipo de gráfico, dimensões) ficam
**sempre visíveis no formulário**, mesmo cadastrando um Termo comum — é uma
decisão deliberada de produto (não há lógica condicional de
mostrar/esconder campo); a orientação para preencher só quando fizer sentido
fica no texto de apoio da tela, não numa regra de validação.

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
| `data_owner` | Dono do dado — busca reaproveitada da mesma busca de usuário dos Data Stewards (workspace/conta), com fallback de entrada manual. |
| `data_steward` | Filtrado automaticamente pelos stewards já cadastrados para o domínio/sub-domínio escolhido; se não houver nenhum, vira campo de texto livre. |
| `rotulo_seguranca` / `rotulo_privacidade` | Dropdowns alimentados pelo mesmo catálogo de **governed tags** (`seguranca`/`privacidade`) usado na Governança de Dados — mantém a classificação do termo consistente com a das colunas. |
| `nivel_apuracao` / `unidade` | Granularidade (ex.: diário, mensal) e unidade de medida. |
| `variaveis_utilizadas` / `fonte_variavel` / `memoria_calculo` | *Só indicador*: quais variáveis entram na conta, de onde vêm, e a fórmula/memória de cálculo. |
| `tipo_grafico` / `dimensoes` | *Só indicador*: como costuma ser visualizado e por quais dimensões é quebrado. |
| `restricoes` | Restrições de uso/acesso relevantes. |

## Onde fica armazenado

Schema **dedicado**, separado do schema de cadastros internos — por padrão
`<CADASTRO_CATALOG>.ontologia_<ENVIRONMENT>` (ex.: `apps.ontologia_prd`).
Ver a nota de configuração em [05. Configuração](./05-configuracao.md): esse
nome de schema **não** tem variável de ambiente própria hoje — está fixo no
código como `"ontologia_" + ENVIRONMENT`, dentro do catálogo de
`CADASTRO_CATALOG`.

## RBAC

Segue o mesmo RBAC dos Cadastros ([06. Permissões](./06-permissoes.md)):
`admin`/`editor` podem criar/editar/excluir; `leitor` só visualiza. Não há
flag de visibilidade própria — a tela aparece junto do grupo Cadastros.

## Integração com o Assistente de IA

Se o módulo de [Assistente de Governança (IA)](./09-modulo-assistente-ia.md)
estiver habilitado, o glossário é uma das fontes de consulta (`tools`)
disponíveis ao modelo — perguntas como "o que é o indicador X?" ou "quem é o
data owner do termo Y?" podem ser respondidas diretamente pelo chat, sem
abrir a tela do glossário.
