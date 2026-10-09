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
       pct_linhas_validas / 100.0 AS pct_validas
FROM execucoes""")},
    {"name": "ds_ultima", "displayName": "Última execução", "queryLines": ql("""
SELECT indicador, tabela, run_time, total_linhas, linhas_validas, linhas_com_erro, linhas_com_aviso, qtd_regras
FROM execucoes
QUALIFY run_time = MAX(run_time) OVER (PARTITION BY indicador_id, tabela)"""),
     "columns": [{"displayName": "Pct Linhas Validas",
                  "description": "Linhas válidas / total de linhas na última verificação",
                  "expression": "SUM(`linhas_validas`) * 1.0 / SUM(`total_linhas`)"}]},
    {"name": "ds_regras", "displayName": "Regras (última execução)", "queryLines": ql("""
SELECT indicador, tabela, run_time, regra_id, regra,
       COALESCE(descricao, regra) AS regra_descricao,
       CASE criticidade WHEN 'error' THEN 'Erro' ELSE 'Aviso' END AS gravidade,
       origem_regra, funcao, linhas_com_falha, total_linhas,
       pct_conformidade / 100.0 AS pct_conformidade,
       CASE WHEN linhas_com_falha > 0 THEN 1 ELSE 0 END AS com_falha,
       CASE WHEN linhas_com_falha > 0 THEN 'Com falha' ELSE 'OK' END AS situacao
FROM metricas_regras
QUALIFY run_time = MAX(run_time) OVER (PARTITION BY indicador_id, tabela)""")},
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
    return {"condition": {"operand": {"type": "data-value", "value": value}, "operator": "="}, "color": {"hex": hexc}}


layout = [
    text("titulo", ["# Qualidade dos Indicadores (DQX)\n", "\n",
                    "Resultado do monitoramento das **regras de qualidade** que os Power Stewards cadastram no Power Steward. "
                    "Os números do topo e as tabelas mostram a **última verificação** de cada indicador; o gráfico de evolução "
                    "mostra o histórico. Nesta fase as regras só **monitoram** — nada é bloqueado no pipeline."], 0, 0, 12, 2),
    counter("kpi_pct_validas", "ds_ultima", fld("measure(Pct Linhas Validas)", "MEASURE(`Pct Linhas Validas`)"),
            "Linhas válidas", 0, PCT),
    counter("kpi_linhas_erro", "ds_ultima", fld("sum(linhas_com_erro)", "SUM(`linhas_com_erro`)"), "Linhas com erro", 3),
    counter("kpi_regras_falha", "ds_regras", fld("sum(com_falha)", "SUM(`com_falha`)"), "Regras com falha", 6),
    counter("kpi_regras", "ds_regras", fld("count(regra_id)", "COUNT(`regra_id`)"), "Regras monitoradas", 9),
    {"widget": {"name": "evolucao_pct",
                "queries": q("ds_hist", [fld("run_time"), fld("indicador"), fld("avg(pct_validas)", "AVG(`pct_validas`)")]),
                "spec": {"version": 3, "widgetType": "line",
                         "encodings": {
                             "x": {"fieldName": "run_time", "scale": {"type": "temporal"}, "displayName": "Verificação"},
                             "y": {"fieldName": "avg(pct_validas)", "scale": {"type": "quantitative"},
                                   "displayName": "% linhas válidas", "format": PCT},
                             "color": {"fieldName": "indicador", "scale": {"type": "categorical"}, "displayName": "Indicador"}},
                         "frame": {"title": "Evolução das linhas válidas", "showTitle": True,
                                   "description": "Uma linha por indicador; cada ponto é uma execução do job DQX.",
                                   "showDescription": True}}},
     "position": pos(0, 5, 6, 6)},
    {"widget": {"name": "falhas_por_regra",
                "queries": q("ds_regras", [fld("regra_descricao"), fld("gravidade"),
                                           fld("sum(linhas_com_falha)", "SUM(`linhas_com_falha`)")]),
                "spec": {"version": 3, "widgetType": "bar",
                         "encodings": {
                             "x": {"fieldName": "sum(linhas_com_falha)", "scale": {"type": "quantitative"},
                                   "displayName": "Linhas com falha"},
                             "y": {"fieldName": "regra_descricao", "scale": {"type": "categorical", "sort": {"by": "value"}},
                                   "displayName": "Regra"},
                             "color": {"fieldName": "gravidade", "displayName": "Gravidade",
                                       "scale": {"type": "categorical",
                                                 "mappings": [{"value": "Erro", "color": BAD}, {"value": "Aviso", "color": WARN}]}}},
                         "frame": {"title": "Linhas com falha por regra", "showTitle": True}}},
     "position": pos(6, 5, 6, 6)},
    text("sec_detalhe", ["## Detalhe por regra\n"], 0, 11, 12, 1),
    table("tabela_regras", "ds_regras", [
        {"fieldName": "situacao", "displayName": "Situação",
         "style": {"type": "basic", "rules": [rule("Com falha", BAD), rule("OK", GOOD)]}},
        {"fieldName": "regra_descricao", "displayName": "Regra"},
        {"fieldName": "gravidade", "displayName": "Gravidade"},
        {"fieldName": "indicador", "displayName": "Indicador"},
        {"fieldName": "tabela", "displayName": "Tabela"},
        {"fieldName": "linhas_com_falha", "displayName": "Linhas com falha"},
        {"fieldName": "total_linhas", "displayName": "Linhas verificadas"},
        {"fieldName": "pct_conformidade", "displayName": "Conformidade", "format": PCT},
        {"fieldName": "origem_regra", "displayName": "Origem"},
    ], "Regras da última verificação", 12, 7, orders=[{"direction": "DESC", "expression": "`linhas_com_falha`"}]),
    text("sec_registros", ["## Registros com falha\n"], 0, 19, 12, 1),
    table("tabela_falhas", "ds_falhas", [
        {"fieldName": "regra_descricao", "displayName": "Regra"},
        {"fieldName": "gravidade", "displayName": "Gravidade"},
        {"fieldName": "colunas", "displayName": "Coluna(s)"},
        {"fieldName": "mensagem", "displayName": "Mensagem do DQX"},
        {"fieldName": "registro", "displayName": "Registro"},
        {"fieldName": "indicador", "displayName": "Indicador"},
        {"fieldName": "tabela", "displayName": "Tabela"},
    ], "Registros reprovados na última verificação", 20, 7),
]

FDS = ["ds_hist", "ds_ultima", "ds_regras", "ds_falhas"]
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
