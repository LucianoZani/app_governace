# 5. Ambientes e sincronização

## Metastore unificado

DEV e PROD compartilham **o mesmo metastore** do Unity Catalog. Os catálogos são
os mesmos objetos vistos pelos dois workspaces; a separação entre ambientes é por
**sufixo de schema**:

| Camada | PROD | DEV |
|---|---|---|
| Gold (catálogos de domínio) | `<schema>` (sem sufixo) | `<schema>_dev` |
| Bronze | `*_bronze_prd` | `*_bronze_dev` |
| Silver | `*_silver_prd` | `*_silver_dev` |

Exemplo (`suprimentos`): `suprimentos.estoque` (PROD) ↔ `suprimentos.estoque_dev` (DEV).

## Isolamento no app

O app é **environment-aware** via `ENVIRONMENT`:

- Função `schema_belongs_to_env(schema)` decide o que aparece na Governança:
  - `ENVIRONMENT=dev` → schemas terminados em `_dev`;
  - `ENVIRONMENT=prd` → schemas **não** terminados em `_dev` (gold sem sufixo +
    `*_bronze_prd`/`*_silver_prd`);
  - `information_schema` e `default` nunca aparecem.
- `ALLOWED_CATALOGS` restringe os catálogos (atual: `suprimentos,rh`).
- **Cadastros também são isolados por ambiente**: o catálogo `apps` é único, mas
  cada ambiente usa seu próprio schema — `apps.governanca_unity_catalog_dev` (DEV) e
  `apps.governanca_unity_catalog_prd` (PROD). O app monta `CADASTRO_SCHEMA + "_" +
  ENVIRONMENT`. Os dados de cadastro de DEV e PROD são, portanto, **isolados**.

> **Fronteira real = grants por schema.** O filtro no app é conveniência de UX; a
> garantia de que "dev não escreve em prod" vem dos **grants por schema do SP** (que
> executa as escritas) e dos grants do usuário nas leituras OBO. Limite os grants de
> `MODIFY`/`APPLY TAG` do SP aos schemas do ambiente (ver [Permissões](./04-permissoes.md)).

## Job `sync_prod_to_dev` (espelho semanal)

`notebooks/utils/sync_prod_to_dev.py` roda **domingo 20h** (somente no workspace
PROD) e mantém DEV como **espelho de PROD**:

1. `CREATE OR REPLACE TABLE <dev> SHALLOW CLONE <prod>` (metadados, sem copiar bytes);
2. copia de PROD para DEV os **comentários (tabela/coluna) e tags** (inclusive
   governadas) que o clone não trouxe;
3. *skip-por-versão*: tabela cuja versão Delta em PROD não mudou é pulada.

### ⚠️ Impacto na governança feita em DEV

- **Escrita em DEV é efêmera.** Sobrevive apenas até a tabela de PROD mudar de
  versão Delta; no próximo sync o clone re-espelha PROD e sobrescreve o que foi
  feito em `*_dev`.
- Tags governadas em colunas `_dev` podem disparar `CANNOT_DROP_TAGGED_COLUMN`
  no `REPLACE`, forçando o job a dropar+recriar a tabela (a tag some).

### O catálogo `apps` (cadastros) NÃO é afetado

O `sync_prod_to_dev` só espelha os catálogos de **dados** (Gold/Bronze/Silver). Ele
**não toca o catálogo `apps`** — portanto **domínios, sub-domínios, data stewards e
permissões não são sobrescritos** pelo sync. Cada ambiente mantém seus cadastros no
próprio schema (`…_dev` / `…_prd`).

### Conclusão

- **DEV = testar o app** (a governança de dados é efêmera por construção).
- **Governança durável = fazer em PROD.** De PROD, comentários e tags **descem
  sozinhos** para DEV no próximo sync (é justamente a fase 2 do job).
- **Cadastros** persistem em ambos os ambientes, independentemente do sync.
