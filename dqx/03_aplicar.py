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
# No job: "{{job.run_id}}" — igual em todas as tentativas da mesma execução, o que deixa a
# gravação idempotente (retry do serverless não duplica o histórico). Vazio = execução manual.
dbutils.widgets.text("job_run_id", "", "Id da execução do job")
CAD = dbutils.widgets.get("cadastros")
RES = dbutils.widgets.get("resultados")
JOB_RUN_ID = dbutils.widgets.get("job_run_id").strip()

import json
import uuid
from datetime import datetime, timezone

import pyspark.sql.functions as F
from pyspark.sql.window import Window
from databricks.sdk import WorkspaceClient
from databricks.labs.dqx.engine import DQEngine

MAX_FALHAS_POR_REGRA = 1000   # o detalhe é amostra; as contagens são sempre completas
dq_engine = DQEngine(WorkspaceClient())
run_id = f"job-{JOB_RUN_ID}" if JOB_RUN_ID else str(uuid.uuid4())
run_time = datetime.now(timezone.utc)

COLS_REGRAS = set(spark.table(f"{CAD}.regras_qualidade").columns)


# Dimensão DAMA: a gravada pelo app; regras antigas (NULL) deduzem pelo tipo/função —
# mesmo mapa do app (`_RQ_DIM_POR_TIPO` / `_RQ_DIM_POR_FUNCAO`).
DIM_POR_TIPO = {"nao_vazio": "completude", "unico": "unicidade", "lista": "validade",
                "intervalo": "validade", "data_futura": "validade", "existe_em": "consistencia",
                "expressao": "consistencia"}
DIM_POR_FUNCAO = {"is_not_null": "completude", "is_not_null_and_not_empty": "completude",
                  "is_not_empty": "completude", "is_unique": "unicidade", "is_in_list": "validade",
                  "is_not_null_and_is_in_list": "validade", "is_in_range": "validade",
                  "is_not_less_than": "validade", "is_not_greater_than": "validade",
                  "is_not_in_future": "validade", "is_valid_date": "validade",
                  "is_valid_timestamp": "validade", "regex_match": "validade",
                  "foreign_key": "consistencia", "sql_expression": "consistencia",
                  "is_data_fresh": "atualidade", "is_older_than_n_days": "atualidade"}


def _dimensao(r):
    return r.dimensao or DIM_POR_TIPO.get(r.tipo) or DIM_POR_FUNCAO.get(r.funcao, "validade")


def _status(pct, faixa_ok, faixa_ruim):
    """Régua do negócio (% de conformidade): OK ≥ faixa_ok; Ruim < faixa_ruim; senão Atenção."""
    if pct is None:
        return None
    return "ok" if pct >= faixa_ok else ("ruim" if pct < faixa_ruim else "atencao")


regras = spark.sql(f"""
    SELECT r.id, r.indicador_id, i.nome AS indicador, r.tabela, r.nome, r.descricao, r.coluna, r.tipo,
           {"r.dimensao" if "dimensao" in COLS_REGRAS else "CAST(NULL AS STRING)"} AS dimensao,
           r.criticidade, r.funcao, r.argumentos, r.origem,
           {"r.escopo" if "escopo" in COLS_REGRAS else "NULL"} AS escopo,
           {"coalesce(r.faixa_ok, 99.0)" if "faixa_ok" in COLS_REGRAS else "99.0"} AS faixa_ok,
           {"coalesce(r.faixa_ruim, 95.0)" if "faixa_ruim" in COLS_REGRAS else "95.0"} AS faixa_ruim
    FROM {CAD}.regras_qualidade r
    LEFT JOIN {CAD}.indicadores i ON i.id = r.indicador_id
    WHERE r.ativa
""").collect()
# escopo: 'lineage' (tabela do indicador, definida pela Engenharia) ou 'montante' (tabela
# escolhida livremente, ex. silver). A montante grava SÓ contagens — o registro reprovado
# ficaria visível a quem vê o painel do indicador, que pode não ter acesso àquela camada.
grupos = {}
for r in regras:
    escopo = "montante" if r.escopo == "montante" else "lineage"
    grupos.setdefault((r.indicador_id, r.indicador, r.tabela, escopo), []).append(r)
print(f"{len(regras)} regras ativas em {len(grupos)} grupo(s) indicador × tabela")

# COMMAND ----------

def _n(coluna):
    """Qtde de issues na linha. O DQX deixa _errors/_warnings NULO quando não há problema."""
    return F.when(F.col(coluna).isNull(), F.lit(0)).otherwise(F.size(coluna))


def _issues(df, coluna, criticidade, sensiveis):
    """Explode _errors/_warnings: 1 linha por registro × regra violada.

    A mensagem do DQX repete o valor ("Value 'x' in Column ..."): se a regra
    toca coluna sensível, o valor sai da mensagem também.
    """
    msg = F.col("i.message")
    if sensiveis:
        toca = F.arrays_overlap(F.coalesce(F.col("i.columns"), F.array().cast("array<string>")),
                                F.array(*[F.lit(c) for c in sensiveis]))
        msg = F.when(toca, F.regexp_replace(msg, r"Value '[^']*'", f"Value '{MASCARA}'")).otherwise(msg)
    return (df.where(_n(coluna) > 0)
              .select(F.col("_registro"), F.explode(coluna).alias("i"))
              .select(F.col("i.name").alias("regra"),
                      F.lit(criticidade).alias("criticidade"),
                      F.col("i.function").alias("funcao"),
                      msg.alias("mensagem"),
                      F.col("i.columns").alias("colunas"),
                      F.col("_registro").alias("registro")))


# Dado pessoal/confidencial não pode vazar em `falhas` (o registro vai inteiro em JSON).
# Mesmos critérios do app: nome casa com Padrões de Dado Pessoal (substring) OU a coluna
# tem tag de compliance (privacidade = dado pessoal / seguranca = confidencial).
MASCARA = "***"
TAGS_SENSIVEIS = {("privacidade", "dado pessoal"), ("seguranca", "confidencial")}
PADROES = [r.padrao.strip().lower() for r in
           spark.sql(f"SELECT padrao FROM {CAD}.padroes_dado_pessoal").collect() if r.padrao]


def _colunas_sensiveis(tabela, colunas):
    """Colunas a mascarar. Se não der pra ler as tags, falha: melhor não gravar do que vazar."""
    cat, sch, tab = tabela.split(".")
    tags = spark.sql(f"""
        SELECT column_name, lower(tag_name) AS k, lower(trim(tag_value)) AS v
        FROM `{cat}`.information_schema.column_tags
        WHERE schema_name = '{sch}' AND table_name = '{tab}'
    """).collect()
    por_tag = {t.column_name for t in tags if (t.k, t.v) in TAGS_SENSIVEIS}
    por_nome = {c for c in colunas if any(p in c.lower() for p in PADROES)}
    return sorted((por_tag | por_nome) & set(colunas))


saida = {"run_id": run_id, "run_time": str(run_time), "grupos": [], "erros": []}
execucoes, metricas, falhas = [], [], []

for (indicador_id, indicador, tabela, escopo), rs in grupos.items():
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
        sensiveis = _colunas_sensiveis(tabela, cols_dados)
        registro = [F.lit(MASCARA).alias(c) if c in sensiveis else F.col(c) for c in cols_dados]
        resultado = (dq_engine.apply_checks_by_metadata(origem, checks)
                     .withColumn("_registro", F.to_json(F.struct(*registro))))
        totais = resultado.agg(
            F.count("*").alias("total"),
            F.sum(F.when(_n("_errors") > 0, 1).otherwise(0)).alias("com_erro"),
            F.sum(F.when(_n("_warnings") > 0, 1).otherwise(0)).alias("com_aviso"),
            F.sum(F.when((_n("_errors") == 0) & (_n("_warnings") == 0), 1).otherwise(0)).alias("validas"),
        ).first()
        issues = (_issues(resultado, "_errors", "error", sensiveis)
                  .unionByName(_issues(resultado, "_warnings", "warn", sensiveis)))
        por_regra = {x["regra"]: x["n"] for x in issues.groupBy("regra").agg(F.count("*").alias("n")).collect()}
    except Exception as exc:  # uma tabela com problema não derruba as outras
        saida["erros"].append({"indicador_id": indicador_id, "tabela": tabela,
                               "erro": f"{type(exc).__name__}: {exc}"[:500]})
        continue

    total = totais["total"] or 0
    base = {"run_id": run_id, "run_time": run_time, "indicador_id": indicador_id,
            "indicador": indicador, "tabela": tabela, "escopo": escopo}
    execucoes.append({**base, "qtd_regras": len(rs), "total_linhas": total,
                      "linhas_com_erro": totais["com_erro"], "linhas_com_aviso": totais["com_aviso"],
                      "linhas_validas": totais["validas"],
                      "pct_linhas_validas": round(100.0 * totais["validas"] / total, 2) if total else None})
    for r in rs:
        n = por_regra.get(r.nome, 0)
        pct = round(100.0 * (total - n) / total, 2) if total else None
        # A régua vai junto: o histórico mostra o critério que valia em cada execução.
        metricas.append({**base, "regra_id": r.id, "regra": r.nome, "descricao": r.descricao,
                         "coluna": r.coluna, "criticidade": r.criticidade, "funcao": r.funcao,
                         "origem_regra": r.origem, "total_linhas": total, "linhas_com_falha": n,
                         "pct_conformidade": pct, "faixa_ok": float(r.faixa_ok),
                         "faixa_ruim": float(r.faixa_ruim), "dimensao": _dimensao(r),
                         "status": _status(pct, float(r.faixa_ok), float(r.faixa_ruim))})

    if escopo == "lineage":
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
    saida["grupos"].append({"indicador": indicador, "tabela": tabela, "escopo": escopo, "regras": len(rs),
                            **totais.asDict(), "falhas_por_regra": por_regra,
                            "colunas_mascaradas": sensiveis})

print(json.dumps(saida, indent=2, default=str, ensure_ascii=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Gravação (append — cada execução vira um ponto da série histórica)

# COMMAND ----------

# Tentativa anterior da mesma execução (retry) pode ter gravado parte: limpa antes do append.
for t in ("execucoes", "metricas_regras", "falhas"):
    if spark.catalog.tableExists(f"{RES}.{t}"):
        spark.sql(f"DELETE FROM {RES}.{t} WHERE run_id = '{run_id}'")

if execucoes:
    spark.createDataFrame(execucoes).write.mode("append").option("mergeSchema", "true").saveAsTable(f"{RES}.execucoes")
    spark.createDataFrame(metricas).write.mode("append").option("mergeSchema", "true").saveAsTable(f"{RES}.metricas_regras")
    if falhas:  # vazio quando só há grupos a montante
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

# COMMAND ----------

# MAGIC %md
# MAGIC ## Resultado do job
# MAGIC Grupo com erro (regra inválida, tabela inacessível…) não impede os outros de gravar,
# MAGIC mas **falha o job** no fim — senão o alerta de falha nunca dispara.

# COMMAND ----------

if saida["erros"]:
    raise RuntimeError(
        f"{len(saida['erros'])} grupo(s) indicador × tabela com erro (os demais foram gravados):\n"
        + json.dumps(saida["erros"], indent=2, ensure_ascii=False))

dbutils.notebook.exit(json.dumps(saida, default=str, ensure_ascii=False))
