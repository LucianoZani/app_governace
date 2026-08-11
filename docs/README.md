# Documentação — App Governança & Cadastros (Unity Catalog)

Documentação completa do Databricks App (Streamlit) com duas áreas:

- **Governança de Dados** — usuários de negócio aplicam/editam **tags governadas**
  e **comentários** em colunas de tabelas do Unity Catalog (executa **on-behalf-of-user**).
- **Cadastros** — Domínios, Sub-domínios, Data Stewards e Permissões, gravados pelo
  **service principal** do app e controlados por papéis (`admin`/`editor`/`leitor`).

> Para um resumo rápido e instruções de deploy essenciais, veja o
> [`../README.md`](../README.md) na raiz do app. Esta pasta contém o
> aprofundamento por tema.

## Índice

| Documento | Conteúdo |
|---|---|
| [1. Visão geral](./01-visao-geral.md) | Objetivo, público-alvo, funcionalidades e escopo. |
| [2. Arquitetura](./02-arquitetura.md) | Componentes, os dois fluxos de auth (OBO e SP), diagramas e decisões técnicas. |
| [3. Configuração](./03-configuracao.md) | Variáveis de ambiente e `app.yaml`. |
| [4. Permissões](./04-permissoes.md) | OBO (governança), RBAC (cadastros), grants e `ASSIGN` de governed tags. |
| [5. Ambientes e sincronização](./05-ambientes-e-sync.md) | Metastore unificado, isolamento dev/prd e o job `sync_prod_to_dev`. |
| [6. Deploy](./06-deploy.md) | Deploy manual pelo Databricks (CLI/UI). |
| [7. Guia de uso](./07-guia-de-uso.md) | Passo a passo para o usuário de negócio. |
| [8. Referência técnica](./08-referencia-tecnica.md) | Estrutura de arquivos e walkthrough do código. |
| [9. Troubleshooting](./09-troubleshooting.md) | Erros comuns e como resolver. |
| [10. Cadastros](./10-cadastros.md) | Domínios, Sub-domínios, Data Stewards e Permissões (RBAC) + catálogo `apps`. |
| [11. Proposta: auto-provisionamento](./11-proposta-auto-provisionamento.md) | **Proposta (não implementado)**: app conceder acesso ao workspace via grupo de conta ao cadastrar usuário. |

## Fatos rápidos

| Item | Valor |
|---|---|
| Nome do app | `governanca-unity-catalog` |
| Áreas | Governança de Dados (OBO) + Cadastros (service principal + RBAC) |
| Página inicial | Governança de Dados — Unity Catalog |
| Service principal (DEV) | `app-446ya1 governanca-unity-catalog` (app id `1b65681b-3435-41db-8e25-114b837e9518`) |
| Service principal (PROD) | SP próprio, distinto do de DEV (criado ao subir o app em PROD) |
| Código-fonte | `apps/governanca-unity-catalog/` (fora do Asset Bundle) |
| Deploy | Manual pelo Databricks (CLI/UI); source recomendado `/Workspace/Shared/apps/governanca-unity-catalog` |
| Warehouse DEV | `eb0314aa7f7a27c2` |
| Warehouse PROD | `99d8bd5c236bde9b` |
| Workspace DEV | `adb-2872092152730919.19` |
| Workspace PROD | `adb-7405615768671067.7` |
| Cadastros | catálogo `apps` (metastore unificado); schema por ambiente: `governanca_unity_catalog_dev` / `_prd` |
