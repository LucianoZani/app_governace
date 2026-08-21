# Governança & Cadastros para Unity Catalog — Documentação do Produto

Um **Databricks App** (Streamlit) que dá a times de dados e de negócio um painel
único para documentar, classificar e governar ativos no **Unity Catalog** — sem
depender do time técnico para cada tag ou comentário, e sem sair dos padrões de
governança definidos pela empresa. Inclui também um assistente de IA para
consulta em linguagem natural e um glossário de termos de negócio/indicadores.

Esta pasta (`docs-produto/`) é a documentação **de produto**: escrita para que
uma pessoa fora do time original consiga **instalar, configurar e operar** o
app em qualquer workspace Databricks com Unity Catalog habilitado — o seu, o de
um cliente da consultoria, ou o de outra empresa do grupo.

> Já existe uma pasta [`../docs/`](../docs/) com documentação técnica **do
> ambiente pessoal atual** do autor (nomes de warehouse, catálogos e schemas
> específicos daquele workspace). Ela continua valendo como referência interna,
> mas não deve ser usada como guia de instalação em outro lugar — para isso,
> use esta pasta.

## Índice

| Documento | Conteúdo |
|---|---|
| [01. Visão geral](./01-visao-geral.md) | O que o produto faz, os módulos, público-alvo, proposta de valor. |
| [02. Arquitetura](./02-arquitetura.md) | Componentes, os modelos de autenticação (OBO / service principal), onde entra o Unity AI Gateway. |
| [03. Pré-requisitos](./03-pre-requisitos.md) | O que o workspace de destino precisa ter **antes** de instalar. |
| [04. Instalação](./04-instalacao.md) | Passo a passo do zero, num workspace novo (CLI e UI manual). |
| [05. Configuração](./05-configuracao.md) | Todas as variáveis de ambiente e o `app.yaml`. |
| [06. Permissões](./06-permissoes.md) | O que cada papel (usuário, service principal, admin) precisa poder fazer. |
| [07. Módulo — Governança de Dados](./07-modulo-governanca-dados.md) | Tags governadas e comentários em tabelas/colunas do UC, e a regra de compliance de dado pessoal. |
| [08. Módulo — Cadastros e Administração](./08-modulo-cadastros.md) | Domínios, sub-domínios, stewards, dashboards, padrões de dado pessoal, backlog de aprovação, usuários & permissões, auditoria. |
| [09. Módulo — Assistente de Governança (IA)](./09-modulo-assistente-ia.md) | Chat com IA sobre o que está cadastrado/governado, via Unity AI Gateway. |
| [10. Módulo — Glossário de Termos de Negócio](./10-modulo-glossario-termos.md) | Cadastro de termos e indicadores (KPIs) com dono, classificação e memória de cálculo. |
| [11. Personalização para um novo cliente](./11-personalizacao-multi-cliente.md) | Checklist do que revisar/trocar ao instalar numa empresa diferente. |
| [12. Troubleshooting](./12-troubleshooting.md) | Erros comuns e como resolver, por módulo. |
| [13. Changelog](./13-changelog.md) | Histórico de versões do produto. |

## Elevator pitch

Times de negócio ganham autonomia para **documentar** o que existe no Unity
Catalog (comentários, tags governadas) sem precisar de acesso de escrita aos
dados; o time de dados mantém **domínios, stewards e um glossário de termos**
centralizados; regras de **compliance de dado pessoal** impedem tagueamento
incorreto sem intervenção manual constante; e um **assistente de IA opcional**
responde perguntas sobre tudo isso em linguagem natural — sem nunca escrever
nada sozinho no Unity Catalog.
