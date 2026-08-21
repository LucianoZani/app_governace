# 11. Personalização para um novo cliente

Checklist para quando esse produto for instalado num workspace Databricks
diferente do original — de outra empresa, ou de outra unidade/ambiente da
mesma empresa.

## 1. Nomes de catálogo e schema

| O quê | Como muda | Onde |
|---|---|---|
| Catálogos de **dados** governados | `ALLOWED_CATALOGS` | `app.yaml` |
| Catálogo dos schemas internos do app | `CADASTRO_CATALOG` (default `apps`) | `app.yaml` |
| Schema de cadastros | `CADASTRO_SCHEMA` + `_<ENVIRONMENT>` | `app.yaml` |
| Schema do glossário de termos | **fixo em código** (`"ontologia_" + ENVIRONMENT`, dentro de `CADASTRO_CATALOG`) | editar `ONTOLOGIA_SCHEMA` em `app.py` se o cliente exigir outro nome |

Nenhum nome de catálogo/schema do ambiente pessoal original (ex.:
`suprimentos`, `governanca_unity_catalog_prd`) deve ser reaproveitado — são
apenas exemplos daquela instalação específica. Defina nomes novos para cada
cliente.

## 2. IDs e identidades específicas do ambiente

Revise e substitua, por instalação:

- `DATABRICKS_WAREHOUSE_ID` — cada workspace tem os seus próprios warehouses.
- O **service principal** do app é criado junto com o app em cada workspace —
  não é reaproveitável entre clientes. Refaça todos os grants de
  [06. Permissões](./06-permissoes.md) para o SP novo.
- `SEED_ADMIN_EMAIL` — use o e-mail do admin inicial **daquele** cliente, não
  o de instalações anteriores.
- `DATABRICKS_ACCOUNT_ID`/`DATABRICKS_ACCOUNT_HOST` — cada conta Databricks
  tem o seu próprio ID; confirme também se o cliente está no Azure (host
  default) ou noutro provedor.

## 3. Unity AI Gateway / Assistente de IA

Antes de prometer o módulo de Assistente de Governança a um novo cliente:

1. Confirme que o workspace dele tem **Unity AI Gateway** habilitado (nem
   toda edição/plano tem).
2. Confirme que existe (ou pode ser criado) um **model service** acessível
   por ele — pode ser um modelo próprio do cliente, um modelo de mercado
   hospedado, ou (se ele tiver credenciais) um provedor externo configurado
   como "external model" no AI Gateway.
3. Se nada disso existir, instale normalmente com `LLM_ENABLED=false` — os
   outros três módulos funcionam sem depender de IA nenhuma. Trate o
   Assistente como um upsell, não como pré-requisito da instalação.
4. Se o modelo escolhido não for o mesmo tipo do testado originalmente
   (família "OSS" com blocos de `reasoning`), teste a resposta do chat antes
   de liberar — ver a nota de compatibilidade em
   [09. Módulo — Assistente de IA](./09-modulo-assistente-ia.md) e
   [12. Troubleshooting](./12-troubleshooting.md).

## 4. Catálogo de Governed Tags

O app **consome** as governed tags já definidas no Unity Catalog do
cliente — ele não vem com nenhuma tag pré-definida. Antes do primeiro uso:

- Confirme que o cliente já tem (ou vai criar) as governed tags relevantes
  no Governance Hub, incluindo as usadas pela regra de compliance de dado
  pessoal (`privacidade`/`seguranca` por padrão — ver
  [07. Módulo — Governança de Dados](./07-modulo-governanca-dados.md)). Se o
  cliente usa outras chaves/valores, ajuste `TAG_COMPLIANCE_RULES` no
  código.
- Cadastre os **Padrões de Dado Pessoal** relevantes para a política de
  privacidade daquele cliente (podem ser bem diferentes de um setor para
  outro).

## 5. Marca e textos

O produto não tem branding embutido além do nome do app e dos textos das
telas (em português). Para um cliente que precise de outro idioma ou de
identidade visual própria, os textos ficam nos `st.markdown`/`st.title`/
`st.caption` espalhados pelo `app.py` — não há um arquivo único de
strings/tema hoje.

## 6. Antes de considerar a instalação pronta

- [ ] Catálogos/schemas do cliente criados e com os grants do SP.
- [ ] `app.yaml` revisado (nenhum valor herdado de outra instalação).
- [ ] Scope `sql` habilitado no app (OBO funcionando).
- [ ] `SEED_ADMIN_EMAIL` do cliente logou pelo menos uma vez e confirma-se
      como admin em Usuários & Permissões.
- [ ] Pelo menos uma governed tag e um domínio cadastrados, para validar o
      fluxo de Governança de ponta a ponta.
- [ ] Se o Assistente de IA for usado: uma pergunta de teste respondida
      corretamente, sem lixo de `reasoning` na tela.
