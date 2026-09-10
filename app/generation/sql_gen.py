"""SQL 生成双引擎

- LLMSQLGenerator : OpenAI 兼容接口 + Prompt 模板路由 + JSON Structured Output
- RuleEngine      : 意图/槽位 → 参数化 SQL 模板（离线回退，保证零 API 依赖可跑通）
统一入口 generate_sql()：配置了 api_base 走 LLM，失败或未配置自动回退规则引擎。
"""
import json
import re
import urllib.request

from app.config import load_config
from app.generation import prompts
from app.generation.validator import QuerySlots, StructuredOutput
from app.schema_linking.linker import render_schema_context

TIME_KEYS = ("time_start", "time_end")


# ---------------------------------------------------------------------------
# 规则模板引擎（离线回退 / 评测基线可用）
# ---------------------------------------------------------------------------

class RuleEngine:
    """按意图族渲染参数化 SQL；槽位来自 DIET + Schema Linking 的联合抽取结果"""

    def __init__(self, row_limit: int = 50):
        self.row_limit = row_limit

    # ---- helpers ----
    @staticmethod
    def _time_filter(aliases: str = "order_date", s: dict | None = None) -> tuple[str, dict]:
        t = (s or {}).get("time") or {}
        if t.get("start") and t.get("end"):
            return f" AND date({aliases}) BETWEEN :start AND :end", {"start": t["start"], "end": t["end"]}
        return "", {}

    @staticmethod
    def _json(intent: str, sql: str, params: dict, chart: str, slots: dict, explain: str) -> StructuredOutput:
        qs = QuerySlots()
        for k, v in slots.items():
            if v is None:
                continue
            if k == "time":
                qs.time_start, qs.time_end, qs.time_grain = v.get("start"), v.get("end"), v.get("grain")
            elif hasattr(qs, k):
                setattr(qs, k, v)
        return StructuredOutput(intent=intent, slots=qs, sql=sql,
                                params=[str(v) for v in params.values()],
                                param_map={k: str(v) for k, v in params.items()},
                                chart=chart, explanation=explain)

    def generate(self, question: str, intent: str, slots: dict, baseline: bool = False) -> StructuredOutput:
        """baseline=True: v1 基线（单一大通用模板，未按意图族细分）"""
        if baseline and intent not in ("sales_query", "order_query"):
            tf, params = self._time_filter("order_date", slots)
            sql = "SELECT COUNT(*) AS order_count FROM orders WHERE 1=1" + tf
            return self._json(intent, sql, params, "table", slots, "通用订单统计模板（v1 基线）")
        handler = getattr(self, f"_gen_{intent}", None)
        if handler is None:
            return self._json(intent, "", {}, "table", slots, "该意图暂不支持 SQL 生成")
        return handler(question, slots)

    # ---- sales_query ----
    def _gen_sales_query(self, q, s):
        tf, params = self._time_filter("order_date", s)
        metric = s.get("metric") or "sales_amount"
        if metric == "order_count":
            sql = "SELECT COUNT(*) AS order_count FROM orders WHERE 1=1" + tf
        else:
            sql = "SELECT ROUND(SUM(total_amount), 2) AS sales_amount FROM orders WHERE status = '已完成'" + tf
        return self._json("sales_query", sql, params, "table", s, "销售聚合统计")

    # ---- order_query ----
    def _gen_order_query(self, q, s):
        tf, params = self._time_filter("order_date", s)
        conds = ""
        if s.get("order_status"):
            conds += " AND status = :status"
            params["status"] = s["order_status"]
        if s.get("pay_method"):
            conds += " AND pay_method = :pay"
            params["pay"] = s["pay_method"]
        if re.search(r"每天|每日|按天|走势|趋势", q):
            base = ("SELECT date(order_date) AS dt, COUNT(*) AS order_count FROM orders "
                    "WHERE 1=1" + tf + conds + " GROUP BY dt ORDER BY dt")
            return self._json("order_query", base, params, "line", s, "按天订单量趋势")
        sql = "SELECT COUNT(*) AS order_count FROM orders WHERE 1=1" + tf + conds
        return self._json("order_query", sql, params, "table", s, "订单数量统计")

    # ---- trend_query ----
    def _gen_trend_query(self, q, s):
        metric = s.get("metric") or "sales_amount"
        tf, params = self._time_filter("o.order_date", s)
        # 时间粒度：每月/按月 → 月粒度，默认按天
        monthly = bool(re.search(r"每月|按月|各月|月度|月走势|月销售额走势", q))
        col = "strftime('%Y-%m', o.order_date)" if monthly else "date(o.order_date)"
        alias = "ym" if monthly else "dt"
        if metric == "sales_volume":
            sql = (f"SELECT {col} AS {alias}, SUM(oi.quantity) AS sales_volume "
                   "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
                   "WHERE o.status = '已完成'" + tf +
                   f" GROUP BY {alias} ORDER BY {alias}")
        elif metric == "order_count":
            sql = (f"SELECT {col} AS {alias}, COUNT(*) AS order_count FROM orders "
                   "WHERE 1=1" + tf + f" GROUP BY {alias} ORDER BY {alias}")
        else:
            sql = (f"SELECT {col} AS {alias}, ROUND(SUM(o.total_amount), 2) AS sales_amount "
                   "FROM orders o WHERE o.status = '已完成'" + tf +
                   f" GROUP BY {alias} ORDER BY {alias}")
        return self._json("trend_query", sql, params, "line", s, "趋势统计")

    # ---- topn_query ----
    def _gen_topn_query(self, q, s):
        dim = s.get("dimension") or "product"
        metric = s.get("metric") or ("sales_volume" if re.search(r"卖得|销量|卖得最多|卖得最好", q) else "sales_amount")
        topn = s.get("topn") or 5
        worst = bool(re.search(r"最差|最不好|最低|最少", q))
        order = "ASC" if worst else "DESC"
        tf, params = self._time_filter("o.order_date", s)
        if dim == "product":
            value = "SUM(oi.quantity)" if metric == "sales_volume" else "ROUND(SUM(oi.subtotal), 2)"
            alias = "sales_volume" if metric == "sales_volume" else "sales_amount"
            sql = (f"SELECT p.product_name, {value} AS {alias} FROM order_items oi "
                   "JOIN orders o ON o.order_id = oi.order_id "
                   "JOIN products p ON p.product_id = oi.product_id "
                   "WHERE o.status = '已完成'" + tf +
                   f" GROUP BY p.product_name ORDER BY {alias} {order} LIMIT {int(topn)}")
        elif dim == "shop":
            sql = ("SELECT s.shop_name, ROUND(SUM(o.total_amount), 2) AS sales_amount "
                   "FROM orders o JOIN shops s ON s.shop_id = o.shop_id "
                   "WHERE o.status = '已完成'" + tf +
                   " GROUP BY s.shop_name ORDER BY sales_amount " + order +
                   (f" LIMIT {int(topn)}" if s.get("topn") else ""))
        elif dim == "customer":
            sql = ("SELECT c.customer_name, ROUND(SUM(o.total_amount), 2) AS total_amount "
                   "FROM orders o JOIN customers c ON c.customer_id = o.customer_id "
                   "WHERE o.status = '已完成'" + tf +
                   f" GROUP BY c.customer_name ORDER BY total_amount {order} LIMIT {int(topn)}")
        else:  # category
            value = "SUM(oi.quantity)" if metric == "sales_volume" else "ROUND(SUM(oi.subtotal), 2)"
            alias = "sales_volume" if metric == "sales_volume" else "sales_amount"
            sql = (f"SELECT c.category_name, {value} AS {alias} FROM order_items oi "
                   "JOIN orders o ON o.order_id = oi.order_id "
                   "JOIN products p ON p.product_id = oi.product_id "
                   "JOIN categories c ON c.category_id = p.category_id "
                   "WHERE o.status = '已完成'" + tf +
                   f" GROUP BY c.category_name ORDER BY {alias} {order} LIMIT {int(topn)}")
        return self._json("topn_query", sql, params, "bar", s, f"{dim} 维度排行 Top{topn}")

    # ---- inventory_query ----
    def _gen_inventory_query(self, q, s):
        if s.get("product"):
            sql = ("SELECT p.product_name, s.shop_name, i.stock_qty, i.safety_stock "
                   "FROM inventory i JOIN products p ON p.product_id = i.product_id "
                   "JOIN shops s ON s.shop_id = i.shop_id "
                   "WHERE p.product_name = :product ORDER BY i.stock_qty DESC")
            return self._json("inventory_query", sql, {"product": s["product"]}, "table", s, "商品各门店库存")
        if re.search(r"库存最多|库存最大", q):
            sql = ("SELECT p.product_name, SUM(i.stock_qty) AS total_stock FROM inventory i "
                   "JOIN products p ON p.product_id = i.product_id "
                   "GROUP BY p.product_name ORDER BY total_stock DESC LIMIT 5")
            return self._json("inventory_query", sql, {}, "bar", s, "库存总量排行")
        sql = ("SELECT p.product_name, SUM(i.stock_qty) AS total_stock FROM inventory i "
               "JOIN products p ON p.product_id = i.product_id "
               "GROUP BY p.product_name ORDER BY total_stock DESC")
        return self._json("inventory_query", sql, {}, "table", s, "全部商品库存总量")

    # ---- stock_alert ----
    def _gen_stock_alert(self, q, s):
        sql = ("SELECT p.product_name, s.shop_name, i.stock_qty, i.safety_stock "
               "FROM inventory i JOIN products p ON p.product_id = i.product_id "
               "JOIN shops s ON s.shop_id = i.shop_id "
               "WHERE i.stock_qty < i.safety_stock ORDER BY i.stock_qty ASC")
        return self._json("stock_alert", sql, {}, "table", s, "低于安全库存的商品清单")

    # ---- customer_query ----
    def _gen_customer_query(self, q, s):
        if re.search(r"消费最高|消费最多|买得最多", q):
            sql = ("SELECT c.customer_name, ROUND(SUM(o.total_amount), 2) AS total_amount "
                   "FROM orders o JOIN customers c ON c.customer_id = o.customer_id "
                   "WHERE o.status = '已完成' GROUP BY c.customer_name "
                   "ORDER BY total_amount DESC LIMIT 1")
            return self._json("customer_query", sql, {}, "table", s, "消费最高客户")
        if re.search(r"新增", q):
            tf, params = self._time_filter("register_date", s)
            sql = "SELECT COUNT(*) AS new_members FROM customers WHERE customer_type = '会员'" + tf
            return self._json("customer_query", sql, params, "table", s, "新增会员数")
        if re.search(r"总数|多少人|多少位|多少名", q):
            if re.search(r"会员", q):
                sql = "SELECT COUNT(*) AS member_count FROM customers WHERE customer_type = '会员'"
            elif re.search(r"散客", q):
                sql = "SELECT COUNT(*) AS casual_count FROM customers WHERE customer_type = '散客'"
            else:
                sql = "SELECT COUNT(*) AS customer_count FROM customers"
            return self._json("customer_query", sql, {}, "table", s, "客户数量统计")
        sql = "SELECT customer_type, COUNT(*) AS cnt FROM customers GROUP BY customer_type ORDER BY cnt DESC"
        return self._json("customer_query", sql, {}, "pie", s, "客户类型分布")

    # ---- product_query ----
    def _gen_product_query(self, q, s):
        if s.get("product"):
            sql = ("SELECT product_name, unit_price, status FROM products "
                   "WHERE product_name = :product")
            return self._json("product_query", sql, {"product": s["product"]}, "table", s, "商品价格查询")
        if s.get("category"):
            sql = ("SELECT p.product_name, p.unit_price FROM products p "
                   "JOIN categories c ON c.category_id = p.category_id "
                   "WHERE c.category_name = :category ORDER BY p.unit_price DESC")
            return self._json("product_query", sql, {"category": s["category"]}, "table", s, "类目商品列表")
        where = " WHERE status = '在售'" if re.search(r"在售", q) else ""
        sql = f"SELECT COUNT(*) AS product_count FROM products{where}"
        return self._json("product_query", sql, {}, "table", s, "商品数量统计")


# ---------------------------------------------------------------------------
# LLM 引擎（OpenAI 兼容 / JSON Structured Output）
# ---------------------------------------------------------------------------

class LLMSQLGenerator:
    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()

    def _call(self, user_prompt: str) -> str:
        g = self.cfg.get("generation", default={})
        body = {
            "model": g.get("model", "qwen-plus"),
            "temperature": g.get("temperature", 0.1),
            "messages": [
                {"role": "system", "content": prompts.SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            g["api_base"].rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {g.get('api_key', '')}"},
        )
        with urllib.request.urlopen(req, timeout=g.get("timeout_seconds", 30)) as resp:
            return json.loads(resp.read())["choices"][0]["message"]["content"]

    def generate(self, question: str, intent: str, slots: dict, linked: dict) -> StructuredOutput | None:
        tpl_name = prompts.route_template(intent)
        tpl = prompts.PROMPTS.get(tpl_name)
        if tpl is None:
            return None
        user_prompt = tpl.format(
            question=question, intent=intent,
            slots=json.dumps({k: v for k, v in slots.items() if v}, ensure_ascii=False, default=str),
            schema=render_schema_context(linked),
            start=(slots.get("time") or {}).get("start", ""),
            end=(slots.get("time") or {}).get("end", ""),
        )
        try:
            raw = self._call(user_prompt)
            data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            return StructuredOutput.model_validate(data)
        except Exception:
            return None


def generate_sql(question: str, intent: str, slots: dict, linked: dict,
                 cfg=None) -> tuple[StructuredOutput, str]:
    """统一入口：返回 (StructuredOutput, engine) ，LLM 失败自动回退规则引擎"""
    cfg = cfg or load_config()
    if cfg.get("generation", "api_base", default=""):
        out = LLMSQLGenerator(cfg).generate(question, intent, slots, linked)
        if out is not None:
            return out, "llm"
    return RuleEngine(cfg.row_limit).generate(question, intent, slots), "rules"
