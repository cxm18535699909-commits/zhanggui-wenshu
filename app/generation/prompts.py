"""可复用 Prompt 模板（4 套）· 按意图族路由

- 00_system.md          : 通用 System 约束（只读、JSON 输出契约）
- 01_sales_aggregate.md : 销售额/订单量聚合类
- 02_trend.md           : 时间序列趋势类
- 03_topn_rank.md       : 排行 TopN 类
- 04_entity_lookup.md   : 实体查询类（商品价格 / 库存 / 客户档案）
"""

SYSTEM = """你是茶饮连锁企业「掌柜茶铺」的数据分析 SQL 专家。任务：根据用户问题、已识别意图与槽位，\
结合给定的数据库 Schema 子集，生成一条 SQLite 方言的只读 SELECT 语句。

硬性约束：
1. 只输出一个 JSON 对象，不要输出任何多余文本，格式：
   {"intent": "...", "slots": {"time_start": null, "time_end": null, "metric": null, ...},
    "sql": "SELECT ...", "params": [], "chart": "table|bar|line|pie", "explanation": "一句话说明"}
2. sql 必须以 SELECT 开头；禁止 INSERT/UPDATE/DELETE/DDL/注释/多语句。
3. 只允许使用给定 Schema 子集中出现的表与字段。
4. 金额字段用 ROUND(SUM(...), 2)；时间过滤用 date(order_date) BETWEEN :start AND :end 形式，
   参数值写入 params（按 :start, :end 顺序），不要把具体日期硬编码进 SQL。
5. 订单有效性口径：status = '已完成' 才计入销售额/销量。
6. 排行类必须 LIMIT {topn}；趋势类必须按天/月 GROUP BY 并 ORDER BY 时间列。
"""

SALES_AGGREGATE = """## 任务类型：销售聚合查询（意图族: sales_query / order_query / customer_query）

用户问题: {question}
识别意图: {intent}
已抽取槽位: {slots}

Schema 子集:
{schema}

### 参考示例
Q: 上个月的销售额是多少 →
{{"intent": "sales_query", "slots": {{"metric": "sales_amount"}}, "sql": "SELECT ROUND(SUM(total_amount), 2) AS sales_amount FROM orders WHERE status = '已完成' AND date(order_date) BETWEEN :start AND :end", "params": ["{start}", "{end}"], "chart": "table", "explanation": "统计上个月已完成订单的销售总额"}}
Q: 会员有多少人 →
{{"intent": "customer_query", "slots": {{"dimension": "customer"}}, "sql": "SELECT COUNT(*) AS member_count FROM customers WHERE customer_type = '会员'", "params": [], "chart": "table", "explanation": "统计会员客户总数"}}

输出 JSON："""

TREND = """## 任务类型：趋势查询（意图族: trend_query）

用户问题: {question}
识别意图: {intent}
已抽取槽位: {slots}

Schema 子集:
{schema}

### 参考示例
Q: 近7天每天的销售额 →
{{"intent": "trend_query", "slots": {{"metric": "sales_amount", "time_grain": "day"}}, "sql": "SELECT date(order_date) AS dt, ROUND(SUM(total_amount), 2) AS sales_amount FROM orders WHERE status = '已完成' AND date(order_date) BETWEEN :start AND :end GROUP BY dt ORDER BY dt", "params": ["{start}", "{end}"], "chart": "line", "explanation": "按天统计销售额趋势"}}

输出 JSON："""

TOPN_RANK = """## 任务类型：排行查询（意图族: topn_query）

用户问题: {question}
识别意图: {intent}
已抽取槽位: {slots}

Schema 子集:
{schema}

### 参考示例
Q: 卖得最好的前5个商品 →
{{"intent": "topn_query", "slots": {{"dimension": "product", "topn": 5, "metric": "sales_amount"}}, "sql": "SELECT p.product_name, ROUND(SUM(oi.subtotal), 2) AS sales_amount FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id WHERE o.status = '已完成' AND date(o.order_date) BETWEEN :start AND :end GROUP BY p.product_name ORDER BY sales_amount DESC LIMIT 5", "params": ["{start}", "{end}"], "chart": "bar", "explanation": "按销售额排行前5商品"}}
Q: 各门店销售额排名 →
{{"intent": "topn_query", "slots": {{"dimension": "shop", "metric": "sales_amount"}}, "sql": "SELECT s.shop_name, ROUND(SUM(o.total_amount), 2) AS sales_amount FROM orders o JOIN shops s ON s.shop_id = o.shop_id WHERE o.status = '已完成' AND date(o.order_date) BETWEEN :start AND :end GROUP BY s.shop_name ORDER BY sales_amount DESC", "params": ["{start}", "{end}"], "chart": "bar", "explanation": "各门店销售额排名"}}

输出 JSON："""

ENTITY_LOOKUP = """## 任务类型：实体查询（意图族: inventory_query / stock_alert / product_query）

用户问题: {question}
识别意图: {intent}
已抽取槽位: {slots}

Schema 子集:
{schema}

### 参考示例
Q: 查一下铁观音的库存 →
{{"intent": "inventory_query", "slots": {{"product": "安溪铁观音"}}, "sql": "SELECT p.product_name, s.shop_name, i.stock_qty, i.safety_stock FROM inventory i JOIN products p ON p.product_id = i.product_id JOIN shops s ON s.shop_id = i.shop_id WHERE p.product_name = :product ORDER BY i.stock_qty DESC", "params": ["安溪铁观音"], "chart": "table", "explanation": "查询该商品在各门店的库存"}}
Q: 哪些商品库存不足 →
{{"intent": "stock_alert", "slots": {{}}, "sql": "SELECT p.product_name, s.shop_name, i.stock_qty, i.safety_stock FROM inventory i JOIN products p ON p.product_id = i.product_id JOIN shops s ON s.shop_id = i.shop_id WHERE i.stock_qty < i.safety_stock ORDER BY i.stock_qty ASC", "params": [], "chart": "table", "explanation": "库存低于安全阈值的商品清单"}}

输出 JSON："""

CHITCHAT = None  # chitchat 意图不生成 SQL

# 模板注册表：意图 → 模板名（Schema Linking + 意图联合路由）
INTENT_TEMPLATE = {
    "sales_query": "sales_aggregate",
    "order_query": "sales_aggregate",
    "customer_query": "sales_aggregate",
    "trend_query": "trend",
    "topn_query": "topn_rank",
    "inventory_query": "entity_lookup",
    "stock_alert": "entity_lookup",
    "product_query": "entity_lookup",
}

PROMPTS = {
    "system": SYSTEM,
    "sales_aggregate": SALES_AGGREGATE,
    "trend": TREND,
    "topn_rank": TOPN_RANK,
    "entity_lookup": ENTITY_LOOKUP,
}


def route_template(intent: str) -> str:
    return INTENT_TEMPLATE.get(intent, "sales_aggregate")
