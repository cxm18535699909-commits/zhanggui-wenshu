"""NL2SQL 评测器 · SQL 执行准确率 + 意图准确率

- SQL 执行准确率：pipeline SQL 与 golden SQL 的执行结果集一致（行集合等价，排序无关）；
  chitchat 样本要求不产出 SQL（正确拒答）。
- 支持基线(v1)/优化版(v2) 双模式对比。
python -m app.cli eval
"""
import json
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text as sql_text

from app.config import load_config
from app.db.schema import get_engine
from app.pipeline.nodes import ask


def _time_params(spec, presets: dict | None = None) -> dict:
    today = date.today()
    if spec is None:
        return {}
    if presets and isinstance(spec, str) and spec in presets:
        spec = presets[spec]  # 解析评测集预设：last_7d → [-6, 0] 等
    if isinstance(spec, list):
        return {"start": str(today + timedelta(days=spec[0])), "end": str(today + timedelta(days=spec[1]))}
    if spec == "month_to_date":
        return {"start": str(today.replace(day=1)), "end": str(today)}
    if spec == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return {"start": str(last_prev.replace(day=1)), "end": str(last_prev)}
    if spec == "week_to_date":
        return {"start": str(today - timedelta(days=today.weekday())), "end": str(today)}
    if spec == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        return {"start": str(start), "end": str(start + timedelta(days=6))}
    if spec == "year_to_date":
        return {"start": str(today.replace(month=1, day=1)), "end": str(today)}
    return {}


def _rows_of(sql: str, params: dict, cfg) -> set | None:
    try:
        with get_engine(cfg).connect() as conn:
            res = conn.execute(sql_text(sql), params if ":start" in sql or ":end" in sql
                               or any(f":{k}" in sql for k in params) else {})
            return {tuple(map(str, r)) for r in res.fetchall()}
    except Exception:
        return None


def run_eval(cfg=None, mode: str = "optimized") -> dict:
    """mode: optimized（v2 全链路）| baseline（v1 基线）"""
    cfg = cfg or load_config()
    eval_path = Path(cfg.get("evaluation", "eval_set", default="app/evaluation/eval_set.json"))
    if not eval_path.is_absolute():
        eval_path = Path(__file__).resolve().parent.parent.parent / eval_path
    with open(eval_path, encoding="utf-8") as f:
        data = json.load(f)

    n, n_sql_ok, n_intent_ok = 0, 0, 0
    per_intent = {}
    errors = []
    for item in data["items"]:
        n += 1
        use_baseline = mode == "baseline"
        out = ask(item["question"], cfg, use_baseline=use_baseline)
        intent_ok = out["intent"] == item["intent"]
        n_intent_ok += intent_ok

        params = {**(item.get("params") or {}), **_time_params(item.get("time"), data.get("time_presets"))}
        if not item["golden_sql"]:
            correct = (not out.get("sql")) and intent_ok  # 拒答样本：无 SQL 且意图正确
        else:
            golden = _rows_of(item["golden_sql"], params, cfg)
            pred = _rows_of(out.get("sql", ""), out.get("sql_params") or {}, cfg) if out.get("ok") else None
            correct = intent_ok and golden is not None and pred == golden
        n_sql_ok += correct

        st = per_intent.setdefault(item["intent"], {"n": 0, "sql_ok": 0, "intent_ok": 0})
        st["n"] += 1
        st["sql_ok"] += correct
        st["intent_ok"] += intent_ok
        if not correct and len(errors) < 10:
            errors.append({"id": item["id"], "question": item["question"], "intent": item["intent"],
                           "pred_intent": out["intent"], "pred_sql": out.get("sql", ""),
                           "reason": "意图错误" if not intent_ok else
                                     ("SQL 执行失败" if not out.get("ok") else "执行结果与 golden 不一致")})

    report = {
        "mode": mode,
        "total": n,
        "sql_exec_accuracy": round(n_sql_ok / n, 4),
        "intent_accuracy": round(n_intent_ok / n, 4),
        "per_intent": {k: {"n": v["n"],
                           "sql_exec_accuracy": round(v["sql_ok"] / v["n"], 4),
                           "intent_accuracy": round(v["intent_ok"] / v["n"], 4)}
                       for k, v in sorted(per_intent.items())},
        "error_samples": errors,
    }
    return report
