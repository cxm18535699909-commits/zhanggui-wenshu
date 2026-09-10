"""SQL 执行与结果结构化

- 只读校验通过后才执行；EXPLAIN 预检语法
- 结果结构化：columns / rows / 摘要文本 / 图表建议
"""
import re

from sqlalchemy import create_engine, text as sql_text

from app.config import load_config
from app.generation.validator import validate_sql

_ENGINE = None


def _get_engine(cfg=None):
    global _ENGINE
    if _ENGINE is None:
        from app.db.schema import get_engine
        _ENGINE = get_engine(cfg)
    return _ENGINE


def _explain_ok(engine, sql: str, params: dict | None = None):
    with engine.connect() as conn:
        stmt = sql_text("EXPLAIN " + sql)
        if params and re.search(r":\w+", sql):
            conn.execute(stmt, params)
        else:
            conn.execute(stmt)


def execute_sql(sql: str, params: dict | None = None, cfg=None) -> dict:
    """执行 SQL → 结构化结果。sql_error 非空表示校验/执行失败。"""
    cfg = cfg or load_config()
    params = params or {}
    ok, msg = validate_sql(sql, explain_fn=lambda s: _explain_ok(_get_engine(cfg), s, params))
    if not ok:
        return {"ok": False, "sql_error": msg, "columns": [], "rows": [], "row_count": 0}

    named = {k: v for k, v in (params or {}).items()}
    try:
        with _get_engine(cfg).connect() as conn:
            bound = sql_text(sql)
            if named and re.search(r":\w+", sql):
                result = conn.execute(bound, named)
            else:
                result = conn.execute(bound)
            columns = list(result.keys())
            rows = [list(map(str, r)) for r in result.fetchmany(cfg.row_limit)]
            row_count = len(rows)
    except Exception as e:
        return {"ok": False, "sql_error": f"执行失败: {e}", "columns": [], "rows": [], "row_count": 0}

    return {"ok": True, "sql_error": "", "columns": columns, "rows": rows, "row_count": row_count}


def summarize(result: dict, chart: str = "table") -> str:
    """根据结果集生成一句话数据摘要"""
    if not result.get("ok"):
        return f"查询失败：{result.get('sql_error')}"
    if result["row_count"] == 0:
        return "查询成功，但没有符合条件的数据。"
    cols, rows = result["columns"], result["rows"]
    if result["row_count"] == 1 and len(cols) == 1:
        return f"查询结果：{cols[0]} = {rows[0][0]}。"
    if chart in ("bar", "pie") or result["row_count"] > 1:
        head = ", ".join(f"{r[0]}={r[1]}" for r in rows[:3])
        more = f" 等 {result['row_count']} 条结果" if result["row_count"] > 3 else ""
        return f"共 {result['row_count']} 条结果：{head}{more}。"
    return f"共 {result['row_count']} 条结果。"
