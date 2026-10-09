"""Gera o dashboard AI/BI "Qualidade dos Indicadores (DQX)" sobre <catalogo>.dqx.*.

Queries usam nomes de tabela sem prefixo; catálogo/schema vêm dos flags do deploy.
No Free: dev.dqx, warehouse 20dfe5c08c3fa359, dashboard 01f1c3d658141785ac33308f8731f848.

    python build.py
    databricks lakeview update <ID> --dataset-catalog dev --dataset-schema dqx \
        --serialized-dashboard "$(cat dashboard.json)"
    databricks lakeview publish <ID> --warehouse-id <WH>
"""
import json, os

os.chdir(os.path.dirname(os.path.abspath(__file__)))


def ql(s):
    return [l + "\n" for l in s.strip().split("\n")]


ds = [
    {"name": "ds_hist", "displayName": "Execuções (histórico)", "queryLines": ql("""
SELECT indicador, tabela, run_id, run_time, total_linhas, linhas_validas, linhas_com_erro, linhas_com_aviso, qtd_regras,
       pct_linhas_validas / 100.0 AS pct_validas,
       CASE WHEN escopo = 'montante' THEN 'A montante' ELSE 'Lineage' END AS escopo,
       concat(indicador, ' · ', CASE WHEN escopo = 'montante' THEN 'A montante' ELSE 'Lineage' END) AS serie
FROM execucoes""")},
    {"name": "ds_ultima", "displayName": "Última execução", "queryLines": ql("""
SELECT indicador, tabela, run_time, total_linhas, linhas_validas, linhas_com_erro, linhas_com_aviso, qtd_regras,
       CASE WHEN escopo = 'montante' THEN 'A montante' ELSE 'Lineage' END AS escopo
FROM execucoes
QUALIFY run_time = MAX(run_time) OVER (PARTITION BY indicador_id, tabela)"""),
     "columns": [{"displayName": "Pct Linhas Validas",
                  "description": "Linhas válidas / total, só nas tabelas do lineage do indicador",
                  "expression": "SUM(CASE WHEN `escopo` = 'Lineage' THEN `linhas_validas` END) * 1.0 / SUM(CASE WHEN `escopo` = 'Lineage' THEN `total_linhas` END)"}]},
    {"name": "ds_regras", "displayName": "Regras (última execução)", "queryLines": ql("""
SELECT indicador, tabela, run_time, regra_id, regra,
       COALESCE(descricao, regra) AS regra_descricao,
       CASE criticidade WHEN 'error' THEN 'Erro' ELSE 'Aviso' END AS gravidade,
       origem_regra, funcao, linhas_com_falha, total_linhas,
       CASE WHEN escopo = 'montante' THEN 'A montante' ELSE 'Lineage' END AS escopo,
       pct_conformidade / 100.0 AS pct_conformidade,
       CASE st WHEN 'ok' THEN '🟢 OK' WHEN 'atencao' THEN '🟡 Atenção' ELSE '🔴 Ruim' END AS status,
       CASE st WHEN 'ok' THEN 0 WHEN 'atencao' THEN 1 ELSE 2 END AS status_ordem,
       CASE WHEN st = 'ok' THEN 1 ELSE 0 END AS is_ok,
       CASE WHEN st = 'atencao' THEN 1 ELSE 0 END AS is_atencao,
       CASE WHEN st = 'ruim' THEN 1 ELSE 0 END AS is_ruim,
       concat('OK ≥ ', format_number(fok, '#.#'), '% · Ruim < ', format_number(fruim, '#.#'), '%') AS regua,
       dimensao_label AS dimensao,
       -- Verificações da tabela (atualidade/acurácia) não têm "linha com falha": resultado em texto.
       CASE WHEN funcao IN ('freshness_sla', 'reconciliacao') THEN NULL ELSE linhas_com_falha END AS falhas_linha,
       coalesce(detalhe, concat(linhas_com_falha, ' de ', total_linhas, ' linha(s) com falha')) AS resultado
FROM (
  -- Régua gravada pelo job em cada execução; execuções antigas usam o padrão 99 / 95.
  SELECT *, coalesce(faixa_ok, 99.0) AS fok, coalesce(faixa_ruim, 95.0) AS fruim,
         CASE coalesce(dimensao, CASE
           WHEN funcao IN ('is_not_null', 'is_not_null_and_not_empty', 'is_not_empty') THEN 'completude'
           WHEN funcao = 'is_unique' THEN 'unicidade'
           WHEN funcao IN ('foreign_key', 'sql_expression') THEN 'consistencia'
           WHEN funcao IN ('is_data_fresh', 'is_older_than_n_days') THEN 'atualidade'
           ELSE 'validade' END)
         WHEN 'completude' THEN '🧩 Completude' WHEN 'unicidade' THEN '🔂 Unicidade'
         WHEN 'validade' THEN '✅ Validade' WHEN 'consistencia' THEN '🔗 Consistência'
         WHEN 'atualidade' THEN '⏱️ Atualidade' ELSE '🎯 Acurácia' END AS dimensao_label,
         coalesce(status, CASE WHEN pct_conformidade >= coalesce(faixa_ok, 99.0) THEN 'ok'
                               WHEN pct_conformidade < coalesce(faixa_ruim, 95.0) THEN 'ruim'
                               ELSE 'atencao' END) AS st
  FROM metricas_regras
)
QUALIFY run_time = MAX(run_time) OVER (PARTITION BY indicador_id, tabela)""")},
    # Status por dimensão DAMA: as 6 sempre aparecem — "Sem regra" mostra a lacuna de cobertura.
    {"name": "ds_dimensao", "displayName": "Status por dimensão DAMA (última execução)", "queryLines": ql("""
WITH ult AS (
  SELECT indicador, coalesce(escopo, 'lineage') AS escopo,
         CASE coalesce(dimensao, CASE
           WHEN funcao IN ('is_not_null', 'is_not_null_and_not_empty', 'is_not_empty') THEN 'completude'
           WHEN funcao = 'is_unique' THEN 'unicidade'
           WHEN funcao IN ('foreign_key', 'sql_expression') THEN 'consistencia'
           WHEN funcao IN ('is_data_fresh', 'is_older_than_n_days') THEN 'atualidade'
           ELSE 'validade' END)
         WHEN 'completude' THEN '🧩 Completude' WHEN 'unicidade' THEN '🔂 Unicidade'
         WHEN 'validade' THEN '✅ Validade' WHEN 'consistencia' THEN '🔗 Consistência'
         WHEN 'atualidade' THEN '⏱️ Atualidade' ELSE '🎯 Acurácia' END AS dimensao,
         coalesce(status, CASE WHEN pct_conformidade >= 99 THEN 'ok'
                               WHEN pct_conformidade < 95 THEN 'ruim' ELSE 'atencao' END) AS status
  FROM metricas_regras
  QUALIFY run_time = MAX(run_time) OVER (PARTITION BY indicador_id)
),
dims AS (
  SELECT * FROM VALUES ('🧩 Completude', 1), ('🔂 Unicidade', 2), ('✅ Validade', 3),
                       ('🔗 Consistência', 4), ('⏱️ Atualidade', 5), ('🎯 Acurácia', 6) AS d(dimensao, ordem)
)
SELECT i.indicador, d.dimensao, d.ordem, count(u.status) AS regras,
       CASE WHEN count(u.status) = 0 THEN '⚪ Sem regra'
            ELSE CASE max(CASE WHEN u.status = 'ruim' AND u.escopo <> 'montante' THEN 2
                               WHEN u.status IN ('ruim', 'atencao') THEN 1 ELSE 0 END)
                 WHEN 2 THEN '🔴 Ruim' WHEN 1 THEN '🟡 Atenção' ELSE '🟢 OK' END END AS status
FROM (SELECT DISTINCT indicador FROM ult) i
CROSS JOIN dims d
LEFT JOIN ult u ON u.indicador = i.indicador AND u.dimensao = d.dimensao
GROUP BY i.indicador, d.dimensao, d.ordem""")},
    # Status do indicador: vale a pior regra; regra a montante é alerta antecipado (no máximo Atenção).
    {"name": "ds_status", "displayName": "Status do indicador (última execução)", "queryLines": ql("""
SELECT indicador, max(run_time) AS run_time,
       CASE max(CASE WHEN status = 'ruim' AND escopo <> 'montante' THEN 2
                     WHEN status IN ('ruim', 'atencao') THEN 1 ELSE 0 END)
            WHEN 2 THEN '🔴 Ruim' WHEN 1 THEN '🟡 Atenção' ELSE '🟢 OK' END AS status_indicador,
       count_if(status = 'ok') AS regras_ok, count_if(status = 'atencao') AS regras_atencao,
       count_if(status = 'ruim') AS regras_ruim
FROM (
  SELECT indicador, run_time, coalesce(escopo, 'lineage') AS escopo,
         coalesce(status, CASE WHEN pct_conformidade >= 99 THEN 'ok'
                               WHEN pct_conformidade < 95 THEN 'ruim' ELSE 'atencao' END) AS status
  FROM metricas_regras
  QUALIFY run_time = MAX(run_time) OVER (PARTITION BY indicador_id)
)
GROUP BY indicador""")},
    {"name": "ds_falhas", "displayName": "Registros com falha (última execução)", "queryLines": ql("""
SELECT f.indicador, f.tabela, f.run_time,
       COALESCE(m.descricao, f.regra) AS regra_descricao,
       CASE f.criticidade WHEN 'error' THEN 'Erro' ELSE 'Aviso' END AS gravidade,
       f.mensagem, COALESCE(NULLIF(array_join(f.colunas, ', '), ''), 'linha inteira') AS colunas, f.registro
FROM falhas f
LEFT JOIN metricas_regras m ON m.run_id = f.run_id AND m.regra_id = f.regra_id
QUALIFY f.run_time = MAX(f.run_time) OVER (PARTITION BY f.indicador_id, f.tabela)""")},
]

BAD, GOOD, WARN = "#E5484D", "#2E9E6B", "#F5A524"
PCT = {"type": "number-percent", "decimalPlaces": {"type": "max", "places": 1}}


def fld(n, e=None):
    return {"name": n, "expression": e or f"`{n}`"}


def q(dsn, fields, dis=False, orders=None):
    qq = {"datasetName": dsn, "fields": fields, "disaggregated": dis}
    if orders:
        qq["orders"] = orders
    return [{"name": "main_query", "query": qq}]


def pos(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def text(name, lines, x, y, w, h):
    return {"widget": {"name": name, "multilineTextboxSpec": {"lines": lines}}, "position": pos(x, y, w, h)}


def counter(name, dsn, f, title, x, fmt=None):
    enc = {"fieldName": f["name"], "displayName": title}
    if fmt:
        enc["format"] = fmt
    return {"widget": {"name": name, "queries": q(dsn, [f]),
                       "spec": {"version": 2, "widgetType": "counter", "encodings": {"value": enc},
                                "frame": {"title": title, "showTitle": True}}},
            "position": pos(x, 2, 3, 3)}


def table(name, dsn, cols, title, y, h, orders=None):
    return {"widget": {"name": name, "queries": q(dsn, [fld(c["fieldName"]) for c in cols], dis=True, orders=orders),
                       "spec": {"version": 2, "widgetType": "table", "encodings": {"columns": cols},
                                "frame": {"title": title, "showTitle": True}}},
            "position": pos(0, y, 12, h)}


def rule(value, hexc):
    return {"condition": {"operand": {"type": "data-value", "value": value}, "operator": "="},
            "backgroundColor": {"hex": hexc}, "color": {"hex": "#FFFFFF"}}


STATUS_RULES = {"type": "basic", "rules": [rule("🟢 OK", GOOD), rule("🟡 Atenção", WARN), rule("🔴 Ruim", BAD)]}
STATUS_MAP = [{"value": "🟢 OK", "color": GOOD}, {"value": "🟡 Atenção", "color": WARN}, {"value": "🔴 Ruim", "color": BAD}]


layout = [
    text("titulo", ["# Qualidade dos Indicadores (DQX)\n", "\n",
                    "Resultado do monitoramento das **regras de qualidade** que os Power Stewards cadastram no Power Steward. "
                    "Os números do topo e as tabelas mostram a **última verificação** de cada indicador; o gráfico de evolução "
                    "mostra o histórico. Cada regra tem uma **régua definida pelo negócio**: 🟢 OK, 🟡 Atenção, 🔴 Ruim. "
                    "O indicador fica com a pior regra. Nesta fase as regras só **monitoram** — nada é bloqueado no pipeline."],
         0, 0, 12, 2),
    counter("kpi_pct_validas", "ds_ultima", fld("measure(Pct Linhas Validas)", "MEASURE(`Pct Linhas Validas`)"),
            "Linhas válidas (lineage)", 0, PCT),
    counter("kpi_ok", "ds_regras", fld("sum(is_ok)", "SUM(`is_ok`)"), "🟢 Regras OK", 3),
    counter("kpi_atencao", "ds_regras", fld("sum(is_atencao)", "SUM(`is_atencao`)"), "🟡 Regras em Atenção", 6),
    counter("kpi_ruim", "ds_regras", fld("sum(is_ruim)", "SUM(`is_ruim`)"), "🔴 Regras Ruins", 9),
    {"widget": {"name": "tabela_status",
                "queries": q("ds_status", [fld(n) for n in ["status_indicador", "indicador", "regras_ok",
                                                             "regras_atencao", "regras_ruim", "run_time"]], dis=True),
                "spec": {"version": 2, "widgetType": "table", "encodings": {"columns": [
                    {"fieldName": "status_indicador", "displayName": "Status", "style": STATUS_RULES},
                    {"fieldName": "indicador", "displayName": "Indicador"},
                    {"fieldName": "regras_ok", "displayName": "🟢 OK"},
                    {"fieldName": "regras_atencao", "displayName": "🟡 Atenção"},
                    {"fieldName": "regras_ruim", "displayName": "🔴 Ruim"},
                    {"fieldName": "run_time", "displayName": "Última verificação"}]},
                    "frame": {"title": "Status do indicador", "showTitle": True,
                              "description": "Vale a pior regra. Regras a montante (ex.: silver) deixam no máximo em Atenção.",
                              "showDescription": True}}},
     "position": pos(0, 5, 5, 4)},
    {"widget": {"name": "tabela_dimensao",
                "queries": q("ds_dimensao", [fld(n) for n in ["dimensao", "status", "regras", "indicador", "ordem"]],
                             dis=True, orders=[{"direction": "ASC", "expression": "`indicador`"},
                                               {"direction": "ASC", "expression": "`ordem`"}]),
                "spec": {"version": 2, "widgetType": "table", "encodings": {"columns": [
                    {"fieldName": "dimensao", "displayName": "Dimensão (DAMA)"},
                    {"fieldName": "status", "displayName": "Status"},
                    {"fieldName": "regras", "displayName": "Regras"},
                    {"fieldName": "indicador", "displayName": "Indicador"}]},
                    "frame": {"title": "Status por dimensão de qualidade (DAMA)", "showTitle": True,
                              "description": "Vale a pior regra da dimensão. ⚪ Sem regra = dimensão ainda não coberta.",
                              "showDescription": True}}},
     "position": pos(5, 5, 7, 4)},
    {"widget": {"name": "evolucao_pct",
                "queries": q("ds_hist", [fld("run_time"), fld("serie"), fld("avg(pct_validas)", "AVG(`pct_validas`)")]),
                "spec": {"version": 3, "widgetType": "line",
                         "encodings": {
                             "x": {"fieldName": "run_time", "scale": {"type": "temporal"}, "displayName": "Verificação"},
                             "y": {"fieldName": "avg(pct_validas)", "scale": {"type": "quantitative"},
                                   "displayName": "% linhas válidas", "format": PCT},
                             "color": {"fieldName": "serie", "scale": {"type": "categorical"}, "displayName": "Indicador · escopo"}},
                         "frame": {"title": "Evolução das linhas válidas", "showTitle": True,
                                   "description": "Uma linha por indicador e escopo (lineage = tabelas do indicador; a montante = ex. silver). Cada ponto é uma execução do job DQX.",
                                   "showDescription": True}}},
     "position": pos(0, 9, 6, 6)},
    {"widget": {"name": "falhas_por_regra",
                "queries": q("ds_regras", [fld("regra_descricao"), fld("status"),
                                           fld("sum(falhas_linha)", "SUM(`falhas_linha`)")]),
                "spec": {"version": 3, "widgetType": "bar",
                         "encodings": {
                             "x": {"fieldName": "sum(falhas_linha)", "scale": {"type": "quantitative"},
                                   "displayName": "Linhas com falha"},
                             "y": {"fieldName": "regra_descricao", "scale": {"type": "categorical", "sort": {"by": "value"}},
                                   "displayName": "Regra"},
                             "color": {"fieldName": "status", "displayName": "Status",
                                       "scale": {"type": "categorical", "mappings": STATUS_MAP}}},
                         "frame": {"title": "Linhas com falha por regra", "showTitle": True}}},
     "position": pos(6, 9, 6, 6)},
    text("sec_detalhe", ["## Detalhe por regra\n"], 0, 15, 12, 1),
    table("tabela_regras", "ds_regras", [
        {"fieldName": "status", "displayName": "Status", "style": STATUS_RULES},
        {"fieldName": "regra_descricao", "displayName": "Regra"},
        {"fieldName": "dimensao", "displayName": "Dimensão"},
        {"fieldName": "gravidade", "displayName": "Gravidade"},
        {"fieldName": "indicador", "displayName": "Indicador"},
        {"fieldName": "tabela", "displayName": "Tabela"},
        {"fieldName": "escopo", "displayName": "Escopo"},
        {"fieldName": "resultado", "displayName": "Resultado"},
        {"fieldName": "pct_conformidade", "displayName": "Conformidade", "format": PCT},
        {"fieldName": "regua", "displayName": "Régua (negócio)"},
        {"fieldName": "origem_regra", "displayName": "Origem"},
    ], "Regras da última verificação", 16, 7, orders=[{"direction": "DESC", "expression": "`status_ordem`"},
                                                    {"direction": "DESC", "expression": "`linhas_com_falha`"}]),
    text("sec_registros", ["## Registros com falha\n",
                           "Só tabelas do lineage — regras a montante guardam apenas contagens.\n"], 0, 23, 12, 1),
    table("tabela_falhas", "ds_falhas", [
        {"fieldName": "regra_descricao", "displayName": "Regra"},
        {"fieldName": "gravidade", "displayName": "Gravidade"},
        {"fieldName": "colunas", "displayName": "Coluna(s)"},
        {"fieldName": "mensagem", "displayName": "Mensagem do DQX"},
        {"fieldName": "registro", "displayName": "Registro"},
        {"fieldName": "indicador", "displayName": "Indicador"},
        {"fieldName": "tabela", "displayName": "Tabela"},
    ], "Registros reprovados na última verificação", 24, 7),
]

FDS = ["ds_hist", "ds_ultima", "ds_regras", "ds_status", "ds_dimensao", "ds_falhas"]
filters = [
    {"widget": {"name": "filtro_indicador",
                "queries": [{"name": f"q_{d}", "query": {"datasetName": d, "fields": [fld("indicador")], "disaggregated": False}}
                            for d in FDS],
                "spec": {"version": 2, "widgetType": "filter-multi-select",
                         "encodings": {"fields": [{"fieldName": "indicador", "queryName": f"q_{d}", "displayName": "Indicador"}
                                                  for d in FDS]},
                         "frame": {"title": "Indicador", "showTitle": True}}},
     "position": pos(0, 0, 4, 2)},
    {"widget": {"name": "filtro_periodo",
                "queries": [{"name": "q_hist_data",
                             "query": {"datasetName": "ds_hist", "fields": [fld("run_time")], "disaggregated": False}}],
                "spec": {"version": 2, "widgetType": "filter-date-range-picker",
                         "encodings": {"fields": [{"fieldName": "run_time", "queryName": "q_hist_data"}]},
                         "frame": {"title": "Período (evolução)", "showTitle": True}}},
     "position": pos(4, 0, 4, 2)},
]

dash = {
    "datasets": ds,
    "pages": [
        {"name": "qualidade", "displayName": "Qualidade", "pageType": "PAGE_TYPE_CANVAS", "layoutVersion": "GRID_V1",
         "layout": layout},
        {"name": "filtros", "displayName": "Filtros", "pageType": "PAGE_TYPE_GLOBAL_FILTERS", "layoutVersion": "GRID_V1",
         "layout": filters},
    ],
    "uiSettings": {"theme": {
        "canvasBackgroundColor": {"light": "#F7F8FA", "dark": "#1F272D"},
        "widgetBackgroundColor": {"light": "#FFFFFF", "dark": "#11171C"},
        "widgetBorderColor": {"light": "#FFFFFF", "dark": "#11171C"},
        "fontColor": {"light": "#11171C", "dark": "#E8ECF0"},
        "selectionColor": {"light": "#2272B4", "dark": "#8ACAFF"},
        "visualizationColors": ["#2272B4", "#F5A524", "#7B61FF", "#2E9E6B", "#DE5582", "#1D425C", "#5ADBFF"],
        "widgetHeaderAlignment": "LEFT"}},
}
with open("dashboard.json", "w", encoding="utf-8") as fh:
    json.dump(dash, fh, ensure_ascii=False)
print("ok")
