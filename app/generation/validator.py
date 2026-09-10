"""SQL 安全校验 + Pydantic 结构化输出 Schema

- StructuredOutput: 约束 LLM/规则引擎输出必须符合的 JSON Schema（对应 JD 的
  JSON Structured Output + Pydantic 校验要求）
- validate_sql: 只读白名单校验（仅 SELECT、表白名单、EXPLAIN 语法检查）
"""
import re

from pydantic import BaseModel, Field, field_validator


class QuerySlots(BaseModel):
    time_start: str | None = None
    time_end: str | None = None
    time_grain: str | None = None
    metric: str | None = None
    dimension: str | None = None
    topn: int | None = Field(default=None, ge=1, le=100)
    product: str | None = None
    category: str | None = None
    shop: str | None = None
    order_status: str | None = None
    pay_method: str | None = None


class StructuredOutput(BaseModel):
    """NL2SQL 全链路的结构化输出契约：意图 + 槽位 + SQL + 图表建议"""
    intent: str
    slots: QuerySlots = Field(default_factory=QuerySlots)
    sql: str = ""
    params: list[str] = Field(default_factory=list)
    param_map: dict[str, str] = Field(default_factory=dict, description="命名参数 :name → 值")
    chart: str = Field(default="table", description="table|bar|line|pie")
    explanation: str = ""

    @field_validator("sql")
    @classmethod
    def sql_must_be_select(cls, v: str) -> str:
        v = v.strip().rstrip(";")
        if v and not v.lstrip().upper().startswith(("SELECT", "WITH")):
            raise ValueError("只允许 SELECT 查询语句")
        return v


ALLOWED_TABLES = {"shops", "categories", "products", "customers",
                  "orders", "order_items", "inventory", "stock_records"}

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|ATTACH|DETACH|PRAGMA|REPLACE)\b",
    re.IGNORECASE)
_COMMENT = re.compile(r"--|/\*|\*/")
_TABS_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)


def validate_sql(sql: str, explain_fn=None) -> tuple[bool, str]:
    """SQL 安全校验。explain_fn: 传入后执行 EXPLAIN 做语法/存在性检查"""
    if not sql or not sql.strip():
        return False, "SQL 为空"
    if _FORBIDDEN.search(sql):
        return False, "包含非只读关键字（INSERT/UPDATE/DELETE/DDL 等）"
    if _COMMENT.search(sql):
        return False, "包含注释符，存在注入风险"
    if ";" in sql.rstrip(";"):
        return False, "不允许多语句"
    for tab in _TABS_RE.findall(sql):
        if tab.lower() not in ALLOWED_TABLES:
            return False, f"访问了白名单之外的表: {tab}"
    if explain_fn is not None:
        try:
            explain_fn(sql)
        except Exception as e:  # 语法错误 / 字段不存在
            return False, f"SQL 语法或字段校验失败: {e}"
    return True, "ok"
