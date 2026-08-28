# 8. Módulo — Cadastros e Administração

Dados internos do app (não são objetos de Unity Catalog) — gravados sempre
pelo **service principal**, com acesso a cada tela controlado pelo **RBAC**
descrito em [06. Permissões](./06-permissoes.md).

## Visão geral das telas

| Grupo (menu) | Telas | Visível para |
|---|---|---|
| **Cadastros** | Domínios · Sub-domínios · Data Owners & Stewards · Dashboards · Padrões de Dado Pessoal · Termos de Negócio (edição) | admin, ou usuário com a flag `ver_cadastros` |
| **Cadastros** (admin) | Usuários & Permissões | só admin |
| **Governança** | Governança de Dados — Unity Catalog | sempre visível |
| **Glossário** | Termos de Negócio (consulta) | sempre visível |
| **Aprovações** | Backlog de Aprovação de Tags | admin, ou usuário com `aprovador_tags` |
| **Auditoria** | Relatório de Auditoria · Log de comentários · Log de tags | admin, ou usuário com `ver_logs` |

Admin enxerga tudo, independente das flags. Dentro de cada tela de cadastro,
**editar** exige papel `admin`/`editor`; `leitor` só visualiza.

## Domínios e Sub-domínios

Estrutura hierárquica simples: um **Domínio** (ex.: "Suprimentos") tem N
**Sub-domínios** (ex.: "Estoque", "Compras"). Usados para agrupar data
stewards, dashboards e termos do glossário. Nome único (domínio globalmente,
sub-domínio por domínio). **Exclusão bloqueada** quando há vínculos: não é
possível excluir um domínio com sub-domínios/stewards, nem um sub-domínio com
stewards.

## Data Owners & Stewards

Vincula uma pessoa (nome + e-mail) a um domínio + sub-domínio — define quem é
a referência daquela área. **Data Owner** e **Data Steward** usam o mesmo
cadastro e a mesma tela: um seletor **Tipo** no topo do formulário
(`Owner` / `Steward`) decide qual dos dois o registro representa. A tabela
lista os dois juntos com a coluna Tipo.

O campo de busca traz usuários do workspace (e da conta, se
`DATABRICKS_ACCOUNT_ID` estiver configurado); se a busca não encontrar
ninguém, um toggle libera entrada manual (nome + e-mail). A unicidade é por
**(tipo, e-mail, domínio, sub-domínio)** — a mesma pessoa pode ser Owner e
Steward do mesmo vínculo, ou responsável por vários domínios, mas não duas
vezes no exato mesmo papel/vínculo.

Instalações criadas antes desta unificação: a coluna `tipo` é adicionada de
forma idempotente no bootstrap e os registros antigos (sem tipo) viram
`Steward`. O filtro de visibilidade de **Dashboards** (ver abaixo) considera
apenas quem for `Steward`.

> Cadastrar alguém aqui só grava o registro — **não** concede nenhum acesso
> ao workspace ou aos dados. Se a pessoa não tiver os grants de Unity Catalog
> e o `CAN_USE` no app, ela não consegue de fato documentar nada, mesmo
> constando como owner/steward.

## Dashboards

Cadastro de links para dashboards (AI/BI, Lakeview ou qualquer URL) já
publicados, vinculados a um Domínio e opcionalmente a um Sub-domínio. Quem
enxerga o link no menu de Governança é quem for **admin** ou **Data Steward**
(tipo `Steward`) daquele domínio/sub-domínio — o módulo reaproveita o
cadastro de Data Owners & Stewards em vez de manter uma lista de acesso
separada.

## Padrões de Dado Pessoal

Lista de padrões (substrings, case-insensitive) de **nome de coluna** que
classificam uma coluna como dado pessoal — ex.: o padrão `cpf` casa com
`numero_cpf`, `cpf_cliente`. Alimenta a regra de compliance de tagueamento
descrita em [07. Módulo — Governança de Dados](./07-modulo-governanca-dados.md).
Cadastre aqui os termos relevantes para a política de privacidade do
cliente (CPF, e-mail, telefone, endereço, etc.) antes de liberar o módulo de
Governança para os stewards.

## Backlog de Aprovação de Tags

Fila de tentativas de tagueamento em colunas de dado pessoal que **não**
cumpriram a regra de compliance (ver módulo de Governança) — ficam com
status `pendente` até um **aprovador** (admin, ou usuário com a flag
`aprovador_tags`) decidir `aprovado`/`rejeitado`, registrando quem decidiu,
quando e o motivo da decisão. A tag só é de fato aplicada no Unity Catalog
se o item for aprovado (a aplicação em si acontece fora deste cadastro, no
fluxo de Governança).

## Usuários & Permissões (admin)

CRUD do RBAC interno: papel (`admin`/`editor`/`leitor`) e as três flags
(`ver_cadastros`, `ver_logs`, `aprovador_tags`) por e-mail. Impede remover o
**último admin** do sistema. Ver detalhes de cada papel/flag em
[06. Permissões](./06-permissoes.md).

## Auditoria (Log de comentários / Log de tags)

Como o **comentário** é escrito pelo service principal (o usuário não tem
`MODIFY`), o Unity Catalog não guarda quem alterou de fato. O app resolve
isso gravando, a cada alteração de comentário ou de tag, o **usuário logado
(OBO)** que solicitou — ação, alvo (catálogo/schema/tabela/coluna), valores
antes/depois e ambiente. As telas de Auditoria mostram os registros com
filtro por usuário e por ação. O registro é *best-effort*: uma falha ao
gravar o log nunca bloqueia a governança em si.

## Modelo de dados (resumo)

Todas as tabelas ficam no schema de cadastros (`CADASTRO_CATALOG` +
`CADASTRO_SCHEMA` + ambiente — ver [05. Configuração](./05-configuracao.md)),
com `id BIGINT GENERATED ALWAYS AS IDENTITY` e colunas de auditoria
(`criado_em`/`criado_por`, `atualizado_em`/`atualizado_por`) nas tabelas de
cadastro propriamente ditas:

| Tabela | Campos principais |
|---|---|
| `dominios` | nome, descricao |
| `subdominios` | dominio_id, nome, descricao |
| `data_stewards` | tipo (`Owner`/`Steward`), dominio_id, subdominio_id, nome, email |
| `dashboards` | dominio_id, subdominio_id, nome, descricao, url, icone, ativo |
| `padroes_dado_pessoal` | padrao, descricao |
| `tag_backlog` | catalogo/schema/tabela/coluna, tag_chave, valor_anterior/novo, acao, motivo, solicitante, status, aprovador, decidido_em, motivo_decisao (append + update no `status`) |
| `permissoes` | email, papel, ver_cadastros, ver_logs, aprovador_tags |
| `log_comentarios` | usuario, acao, objeto, catalogo/schema/tabela/coluna, comentario_anterior/novo, ambiente, criado_em (append-only) |
| `log_tags` | usuario, acao, catalogo/schema/tabela/coluna, tag_chave, valor_anterior/novo, ambiente, criado_em (append-only) |

> O Unity Catalog/Delta não força unicidade nem chave estrangeira — o app
> valida em código (INSERTs usam guard atômico `WHERE NOT EXISTS`).
