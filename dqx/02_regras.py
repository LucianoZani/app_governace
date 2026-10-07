# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Regras revisadas → tabela UC
# MAGIC
# MAGIC Passo 2 do roteiro. As regras revisadas (`checks/fct_pedidos.yml`) são:
# MAGIC 1. **validadas** pelo DQX (função existe? argumentos certos?);
# MAGIC 2. gravadas na tabela **`dev.dqx.regras_qualidade`** — o formato que um app
# MAGIC    (ex. Power Steward) preencheria;
# MAGIC 3. lidas de volta da tabela e de uma **view**, para provar que o job do DQX
# MAGIC    consegue consumi-las.

# COMMAND ----------

# MAGIC %pip install databricks-labs-dqx==0.16.0
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import json
from databricks.sdk import WorkspaceClient
from databricks.labs.dqx.config import TableChecksStorageConfig, WorkspaceFileChecksStorageConfig
from databricks.labs.dqx.engine import DQEngine

ws = WorkspaceClient()
usuario = ws.current_user.me().user_name
YAML_PATH = f"/Workspace/Users/{usuario}/dqx/checks/fct_pedidos.yml"
TABELA_REGRAS = "dev.dqx.regras_qualidade"
VIEW_REGRAS = "dev.dqx.vw_regras_qualidade_error"
ALVO = "dev.gold.fct_pedidos"   # run_config_name = tabela onde as regras se aplicam

dq_engine = DQEngine(ws)
saida = {}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Ler e validar

# COMMAND ----------

checks = dq_engine.load_checks(config=WorkspaceFileChecksStorageConfig(location=YAML_PATH))
status = dq_engine.validate_checks(checks)
print(f"{len(checks)} regras lidas do YAML | erros de validação: {status.has_errors}")
print(status)
saida["regras_yaml"] = len(checks)
saida["validacao_erros"] = status.has_errors
saida["validacao_detalhe"] = str(status)
assert not status.has_errors, "Corrigir o YAML antes de gravar"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Gravar na tabela UC
# MAGIC `mode="overwrite"` troca só as regras deste `run_config_name`; se nada mudou
# MAGIC (mesmo *fingerprint*), o DQX não regrava.

# COMMAND ----------

dq_engine.save_checks(checks, config=TableChecksStorageConfig(
    location=TABELA_REGRAS, run_config_name=ALVO, mode="overwrite"))

linhas = spark.sql(f"""
    SELECT name, criticality, check.function AS funcao, check.arguments AS argumentos,
           run_config_name, user_metadata, created_at, rule_fingerprint
    FROM {TABELA_REGRAS} WHERE run_config_name = '{ALVO}' ORDER BY name""")
display(linhas)
saida["schema_tabela"] = spark.table(TABELA_REGRAS).schema.simpleString()
saida["linhas_tabela"] = [r.asDict(recursive=True) for r in linhas.collect()]

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Ler de volta (tabela e view)

# COMMAND ----------

da_tabela = dq_engine.load_checks(config=TableChecksStorageConfig(location=TABELA_REGRAS, run_config_name=ALVO))
saida["regras_lidas_tabela"] = len(da_tabela)
saida["ida_e_volta_igual"] = sorted(json.dumps(c, sort_keys=True, default=str) for c in da_tabela) == \
                             sorted(json.dumps(c, sort_keys=True, default=str) for c in checks)
saida["exemplo_lido_status"] = next(c for c in da_tabela if c.get("name") == "status_invalido")

# Uma view simula "só as regras ativas/aprovadas" que o app exporia ao job
spark.sql(f"CREATE OR REPLACE VIEW {VIEW_REGRAS} AS SELECT * FROM {TABELA_REGRAS} WHERE criticality = 'error'")
try:
    da_view = dq_engine.load_checks(config=TableChecksStorageConfig(location=VIEW_REGRAS, run_config_name=ALVO))
    saida["regras_lidas_view"] = len(da_view)
except Exception as e:  # registrar o resultado do teste, seja qual for
    saida["regras_lidas_view"] = f"FALHOU: {type(e).__name__}: {e}"
print(json.dumps(saida, indent=2, default=str, ensure_ascii=False))

# COMMAND ----------

dbutils.notebook.exit(json.dumps(saida, default=str, ensure_ascii=False))
