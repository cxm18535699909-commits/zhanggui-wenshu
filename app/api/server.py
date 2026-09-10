"""FastAPI 服务 · /api/ask /api/eval /api/schema /api/stats"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

from app.config import load_config  # noqa: E402
from app.db.schema import get_engine  # noqa: E402
from app.pipeline.nodes import ask  # noqa: E402
from app.schema_linking.catalog import TABLE_CATALOG  # noqa: E402

app = FastAPI(title="掌柜问数 · NL2SQL 智能数据分析系统", version="1.0.0")


class AskRequest(BaseModel):
    question: str


@app.post("/api/ask")
def api_ask(req: AskRequest):
    return ask(req.question, load_config())


@app.get("/api/schema")
def api_schema():
    return {t: {"zh": v["zh"], "description": v["description"], "columns": list(v["columns"])}
            for t, v in TABLE_CATALOG.items()}


@app.get("/api/stats")
def api_stats():
    cfg = load_config()
    with get_engine(cfg).connect() as conn:
        def one(sql):
            return conn.execute(sql_text(sql)).scalar()
        return {
            "products": one("SELECT COUNT(*) FROM products"),
            "orders": one("SELECT COUNT(*) FROM orders"),
            "customers": one("SELECT COUNT(*) FROM customers"),
            "inventory_records": one("SELECT COUNT(*) FROM inventory"),
            "tables": len(TABLE_CATALOG),
        }


@app.get("/api/eval")
def api_eval():
    path = Path(__file__).resolve().parent.parent.parent / "data/logs/eval_report.json"
    if path.exists():
        import json
        return json.loads(path.read_text(encoding="utf-8"))
    return {"message": "尚未运行评测，请先执行 python -m app.cli eval"}


@app.get("/")
def index():
    return FileResponse(Path(__file__).resolve().parent.parent.parent / "web/index.html")
