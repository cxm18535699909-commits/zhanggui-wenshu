"""槽位归一化：自然语言槽位 → 结构化查询参数

- TIME  → {start, end, grain} 日期区间
- METRIC → 销售额 / 订单量 / 销量 / 利润
- DIMENSION → product / shop / customer / category / pay_method
- PRODUCT / CATEGORY → 经词典对齐到数据库真实实体名
"""
import re
from datetime import date, timedelta


# ---------------- 时间解析 ----------------

def parse_time(text: str, today: date | None = None) -> dict | None:
    today = today or date.today()

    def rng(s: date, e: date, grain: str = "day") -> dict:
        return {"start": s.isoformat(), "end": e.isoformat(), "grain": grain}

    if re.search(r"今天|今日", text):
        return rng(today, today)
    if re.search(r"昨天|昨日", text):
        y = today - timedelta(days=1)
        return rng(y, y)
    if re.search(r"前天", text):
        d = today - timedelta(days=2)
        return rng(d, d)
    if re.search(r"本(周|星期|礼拜)", text):
        s = today - timedelta(days=today.weekday())
        return rng(s, today)
    if re.search(r"上(周|星期|礼拜)", text):
        s = today - timedelta(days=today.weekday() + 7)
        return rng(s, s + timedelta(days=6))
    m = re.search(r"(?:这|本)个?月", text)
    if m:
        s = today.replace(day=1)
        return rng(s, today)
    m = re.search(r"(上|去)个?月", text)
    if m:
        first = today.replace(day=1)
        s = (first - timedelta(days=1)).replace(day=1)
        e = first - timedelta(days=1)
        return rng(s, e)
    m = re.search(r"今年|今年以来|这一年", text)
    if m:
        return rng(date(today.year, 1, 1), today)
    if re.search(r"去年", text):
        return rng(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31))
    # 近/最近/过去 N 天
    m = re.search(r"(?:近|最近|过去)(\d+)天", text)
    if m:
        n = int(m.group(1))
        return rng(today - timedelta(days=n - 1), today)
    m = re.search(r"(?:近|最近|过去)(\d+)个?月", text)
    if m:
        n = int(m.group(1))
        return rng(today - timedelta(days=30 * n - 1), today)
    m = re.search(r"(?:近|最近|过去)(\d+)年", text)
    if m:
        n = int(m.group(1))
        return rng(today.replace(year=today.year - n + 1, day=1), today)
    if re.search(r"最近一周|过去一周|近一周", text):
        return rng(today - timedelta(days=6), today)
    if re.search(r"最近一个月|近一个月|过去一个月", text):
        return rng(today - timedelta(days=29), today)
    if re.search(r"最近|近期|这几天", text):
        return rng(today - timedelta(days=6), today)
    return None


# ---------------- 指标 / 维度 ----------------

METRIC_MAP = [
    (r"销售额|销售总额|营业额|营业收入|营收|卖了多少钱|成交金额|实收金额|卖了|销售情况|销售业绩|业绩|销售|金额", "sales_amount"),
    (r"订单量|订单数|订单|笔数|几单|多少笔|多少单|单量", "order_count"),
    (r"销量|售出|卖出多少|卖了多少件|件数|卖得|卖得最多|卖得最好|卖得最差", "sales_volume"),
    (r"利润|毛利", "profit"),
]


def parse_metric(text: str) -> str | None:
    for pat, metric in METRIC_MAP:
        if re.search(pat, text):
            return metric
    return None


def parse_dimension(text: str) -> str | None:
    if re.search(r"商品|货品|产品|货物", text):
        return "product"
    if re.search(r"门店|店铺|分店|店面", text):
        return "shop"
    if re.search(r"客户|会员|散客|顾客|买家", text):
        return "customer"
    if re.search(r"类目|品类|分类", text):
        return "category"
    if re.search(r"微信|支付宝|现金|银行卡", text):
        return "pay_method"
    return None


def parse_topn(text: str) -> int | None:
    cn = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    m = re.search(r"(?:top|TOP)\s*(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"前\s*(\d+)\s*[个位名]", text)
    if m:
        return int(m.group(1))
    m = re.search(r"前\s*([一二两三四五六七八九十])\s*[个位名]?", text)
    if m:
        return cn[m.group(1)]
    if re.search(r"前三", text):
        return 3
    # 最高/最好/最差/排行 语境下的数量词（含中文数字）
    if re.search(r"最高|最好|最多|最差|最少|排行|排名", text):
        m = re.search(r"(\d+)\s*[个名位]", text)
        if m:
            return int(m.group(1))
        m = re.search(r"([一二两三四五六七八九十])\s*[个名位]", text)
        if m:
            return cn[m.group(1)]
    return None


def parse_order_status(text: str) -> str | None:
    for s in ["已完成", "待付款", "已退款"]:
        if s in text:
            return s
    return None


def parse_pay_method(text: str) -> str | None:
    for p in ["微信支付", "支付宝", "现金", "银行卡"]:
        if p in text or (p == "微信支付" and "微信" in text):
            return p
    return None


# ---------------- 实体词典对齐 ----------------

def align_entity(text: str, dictionary: list[str], alias_map: dict | None = None,
                 fuzzy_threshold: float = 0.55) -> tuple[str | None, float]:
    """实体对齐三级策略：全名包含 → 别名包含 → 字符重合率模糊匹配"""
    alias_map = alias_map or {}
    for name in dictionary:  # 1. 全名包含
        if name in text:
            return name, 1.0
    for alias, canon in alias_map.items():  # 2. 别名包含（龙井→西湖龙井）
        if alias in text:
            return canon, 0.95
    best, score = None, 0.0
    for name in dictionary:  # 3. 字符重合率模糊匹配
        chars = set(name) & set(text)
        if not chars:
            continue
        overlap = len(chars) / len(set(name))
        if overlap > score and overlap >= fuzzy_threshold:
            best, score = name, overlap
    return best, round(score, 4)


def extract_slots(text: str, entity_dict: dict, fuzzy_threshold: float = 0.55) -> dict:
    """从问句抽取全部结构化槽位（DIET 槽位检测 + 词典对齐）"""
    product, p_score = align_entity(text, entity_dict.get("products", []),
                                    entity_dict.get("product_alias"), fuzzy_threshold)
    category, _ = align_entity(text, entity_dict.get("categories", []),
                               entity_dict.get("category_alias"), fuzzy_threshold)
    shop, _ = align_entity(text, entity_dict.get("shops", []),
                           entity_dict.get("shop_alias"), fuzzy_threshold)
    return {
        "time": parse_time(text),
        "metric": parse_metric(text),
        "dimension": parse_dimension(text),
        "topn": parse_topn(text),
        "product": product,
        "category": category if product is None else None,
        "shop": shop,
        "order_status": parse_order_status(text),
        "pay_method": parse_pay_method(text),
    }
