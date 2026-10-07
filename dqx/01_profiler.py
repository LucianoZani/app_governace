# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Profiler DQX em `dev.gold.fct_pedidos`
# MAGIC
# MAGIC Passo 1 do roteiro: o **DQProfiler** lê a tabela e calcula estatísticas por coluna
# MAGIC (nulos, mín/máx, valores distintos…); o **DQGenerator** converte essas estatísticas
# MAGIC em **regras candidatas**. As regras saem em YAML para revisão humana — nada é aplicado aqui.

# COMMAND ----------

# MAGIC %pip install databricks-labs-dqx==0.16.0
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import json
import yaml
from databricks.sdk import WorkspaceClient
from databricks.labs.dqx.config import InputConfig, WorkspaceFileChecksStorageConfig
from databricks.labs.dqx.engine import DQEngine
from databricks.labs.dqx.profiler.generator import DQGenerator
from databricks.labs.dqx.profiler.profiler import DQProfiler

# Colunas decimal(12,2) geram limites como Decimal('88.00'), que o PyYAML não serializa.
# Ensina o SafeDumper a escrevê-los como número (vale também para o save_checks do DQX).
from decimal import Decimal
yaml.SafeDumper.add_representer(Decimal, lambda d, v: d.represent_float(float(v)))

TABELA = "dev.gold.fct_pedidos"
ws = WorkspaceClient()
usuario = ws.current_user.me().user_name
CHECKS_PATH = f"/Workspace/Users/{usuario}/dqx/checks_fct_pedidos_candidatas.yml"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Profiling
# MAGIC Padrão do DQX: amostra 30% e no máx. 1000 linhas. A tabela tem poucas linhas,
# MAGIC então usamos 100% para o profile refletir a tabela inteira.

# COMMAND ----------

profiler = DQProfiler(ws)
summary_stats, profiles = profiler.profile_table(
    input_config=InputConfig(location=TABELA),
    options={"sample_fraction": 1.0, "limit": 1000},
)

print(json.dumps(summary_stats, indent=2, default=str))
for p in profiles:
    print(p)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Geração das regras candidatas

# COMMAND ----------

generator = DQGenerator(ws)
checks = generator.generate_dq_rules(profiles)  # criticidade padrão: error
print(yaml.safe_dump(checks, sort_keys=False, allow_unicode=True))

# COMMAND ----------

dq_engine = DQEngine(ws)
dq_engine.save_checks(checks, config=WorkspaceFileChecksStorageConfig(location=CHECKS_PATH))
print("Regras salvas em", CHECKS_PATH)

# COMMAND ----------

dbutils.notebook.exit(json.dumps({
    "checks_path": CHECKS_PATH,
    "summary_stats": summary_stats,
    "profiles": [str(p) for p in profiles],
    "checks": checks,
}, default=str))
