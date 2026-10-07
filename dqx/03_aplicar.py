# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Aplicar as regras do app e gravar os resultados (modo monitoramento)
# MAGIC
# MAGIC As regras são escritas pelo Power Steward no app (página **Regras de Qualidade**) e
# MAGIC ficam em `<cadastros>.regras_qualidade`, já no formato do DQX (`funcao` + `argumentos`).
# MAGIC Este job lê as regras **ativas**, aplica com o DQX por **indicador × tabela** e grava:
# MAGIC
# MAGIC | Tabela | Grão | Uso |
# MAGIC |---|---|---|
# MAGIC | `<resultados>.execucoes` | indicador × tabela × execução | % de linhas válidas, tendência |
# MAGIC | `<resultados>.metricas_regras` | regra × execução | ranking de regras, % de conformidade |
# MAGIC | `<resultados>.falhas` | registro × regra violada | detalhe (amostra por regra) |
# MAGIC
# MAGIC **Modo monitoramento:** só lê as tabelas de origem — não copia dados nem altera pipelines.

# COMMAND ----------

# MAGIC %pip install databricks-labs-dqx==0.16.0
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("cadastros", "apps.governanca_unity_catalog_prd", "Schema de cadastros do app")
dbutils.widgets.text("resultados", "dev.dqx", "Schema dos resultados")
CAD = dbutils.widgets.get("cadastros")
RES = dbutils.widgets.get("resultados")

import json
import uuid
from datetime import datetime, timezone

import pyspark.sql.functions as F
from pyspark.sql.window import Window
from databricks.sdk import WorkspaceClient
from databricks.labs.dqx.engine import DQEngine

MAX_FALHAS_POR_REGRA = 1000   # o detalhe é amostra; as contagens são sempre completas
dq_engine = DQEngine(WorkspaceClient())
run_id = str(uuid.uuid4())
run_time = datetime.now(timezone.utc)

regras = spark.sql(f"""
    SELECT r.id, r.indicador_id, i.nome AS indicador, r.tabela, r.nome, r.descricao, r.coluna,
           r.criticidade, r.funcao, r.argumentos, r.origem
    FROM {CAD}.regras_qualidade r
    LEFT JOIN {CAD}.indicadores i ON i.id = r.indicador_id
    WHERE r.ativa
""").collect()
grupos = {}
for r in regras:
    grupos.setdefault((r.indicador_id, r.indicador, r.tabela), []).append(r)
print(f"{len(regras)} regras ativas em {len(grupos)} grupo(s) indicador × tabela")

# COMMAND ----------

def _n(coluna):
    """Qtde de issues na linha. O DQX deixa _errors/_warnings NULO quando não há problema."""
    return F.when(F.col(coluna).isNull(), F.lit(0)).otherwise(F.size(coluna))


def _issues(df, coluna, criticidade):
    """Explode _errors/_warnings: 1 linha por registro × regra violada."""
    return (df.where(_n(coluna) > 0)
              .select(F.col("_registro"), F.explode(coluna).alias("i"))
              .select(F.col("i.name").alias("regra"),
                      F.lit(criticidade).alias("criticidade"),
                      F.col("i.function").alias("funcao"),
                      F.col("i.message").alias("mensagem"),
                      F.col("i.columns").alias("colunas"),
                      F.col("_registro").alias("registro")))


saida = {"run_id": run_id, "run_time": str(run_time), "grupos": [], "erros": []}
execucoes, metricas, falhas = [], [], []

for (indicador_id, indicador, tabela), rs in grupos.items():
    checks = [{
        "name": r.nome,
        "criticality": r.criticidade,
        "check": {"function": r.funcao, "arguments": json.loads(r.argumentos or "{}")},
        "user_metadata": {"indicador_id": str(indicador_id), "regra_id": str(r.id),
                          "origem": r.origem or "app"},
    } for r in rs]
    status = dq_engine.validate_checks(checks)
    if status.has_errors:
        saida["erros"].append({"indicador_id": indicador_id, "tabela": tabela, "erro": str(status)})
        continue
    try:
        origem = spark.table(tabela)
        cols_dados = [c for c in origem.columns if not c.startswith("_")]
        resultado = (dq_engine.apply_checks_by_metadata(origem, checks)
                     .withColumn("_registro", F.to_json(F.struct(*cols_dados))))
        totais = resultado.agg(
            F.count("*").alias("total"),
            F.sum(F.when(_n("_errors") > 0, 1).otherwise(0)).alias("com_erro"),
            F.sum(F.when(_n("_warnings") > 0, 1).otherwise(0)).alias("com_aviso"),
            F.sum(F.when((_n("_errors") == 0) & (_n("_warnings") == 0), 1).otherwise(0)).alias("validas"),
        ).first()
        issues = _issues(resultado, "_errors", "error").unionByName(_issues(resultado, "_warnings", "warn"))
        por_regra = {x["regra"]: x["n"] for x in issues.groupBy("regra").agg(F.count("*").alias("n")).collect()}
    except Exception as exc:  # uma tabela com problema não derruba as outras
        saida["erros"].append({"indicador_id": indicador_id, "tabela": tabela,
                               "erro": f"{type(exc).__name__}: {exc}"[:500]})
        continue

    total = totais["total"] or 0
    base = {"run_id": run_id, "run_time": run_time, "indicador_id": indicador_id,
            "indicador": indicador, "tabela": tabela}
    execucoes.append({**base, "qtd_regras": len(rs), "total_linhas": total,
                      "linhas_com_erro": totais["com_erro"], "linhas_com_aviso": totais["com_aviso"],
                      "linhas_validas": totais["validas"],
                      "pct_linhas_validas": round(100.0 * totais["validas"] / total, 2) if total else None})
    for r in rs:
        n = por_regra.get(r.nome, 0)
        metricas.append({**base, "regra_id": r.id, "regra": r.nome, "descricao": r.descricao,
                         "coluna": r.coluna, "criticidade": r.criticidade, "funcao": r.funcao,
                         "origem_regra": r.origem, "total_linhas": total, "linhas_com_falha": n,
                         "pct_conformidade": round(100.0 * (total - n) / total, 2) if total else None})

    ids = spark.createDataFrame([(r.nome, r.id) for r in rs], "regra string, regra_id long")
    w = Window.partitionBy("regra").orderBy("registro")
    falhas.append(issues.withColumn("_rn", F.row_number().over(w))
                        .where(F.col("_rn") <= MAX_FALHAS_POR_REGRA).drop("_rn")
                        .join(ids, "regra", "left")
                        .withColumn("run_id", F.lit(run_id))
                        .withColumn("run_time", F.lit(run_time))
                        .withColumn("indicador_id", F.lit(indicador_id).cast("long"))
                        .withColumn("indicador", F.lit(indicador))
                        .withColumn("tabela", F.lit(tabela)))
    saida["grupos"].append({"indicador": indicador, "tabela": tabela, "regras": len(rs),
                            **totais.asDict(), "falhas_por_regra": por_regra})

print(json.dumps(saida, indent=2, default=str, ensure_ascii=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Gravação (append — cada execução vira um ponto da série histórica)

# COMMAND ----------

if execucoes:
    spark.createDataFrame(execucoes).write.mode("append").option("mergeSchema", "true").saveAsTable(f"{RES}.execucoes")
    spark.createDataFrame(metricas).write.mode("append").option("mergeSchema", "true").saveAsTable(f"{RES}.metricas_regras")
    df_falhas = falhas[0]
    for f in falhas[1:]:
        df_falhas = df_falhas.unionByName(f)
    df_falhas.write.mode("append").option("mergeSchema", "true").saveAsTable(f"{RES}.falhas")
    for t, desc in [
        ("execucoes", "DQX — resumo por indicador, tabela e execução (modo monitoramento)."),
        ("metricas_regras", "DQX — resultado por regra e execução: linhas com falha e % de conformidade."),
        ("falhas", "DQX — registros que violaram regras (amostra limitada por regra), por execução."),
    ]:
        spark.sql(f"COMMENT ON TABLE {RES}.{t} IS '{desc}'")
    saida["gravado"] = {t: spark.table(f"{RES}.{t}").where(F.col("run_id") == run_id).count()
                        for t in ("execucoes", "metricas_regras", "falhas")}
    print(saida["gravado"])

if saida["erros"]:
    print("⚠️ grupos com erro:", json.dumps(saida["erros"], indent=2, ensure_ascii=False))

# COMMAND ----------

dbutils.notebook.exit(json.dumps(saida, default=str, ensure_ascii=False))
