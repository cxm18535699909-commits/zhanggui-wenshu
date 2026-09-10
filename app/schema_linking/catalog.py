"""Schema 目录 · 表/字段级中文语义描述 + 同义词词典

Schema Linking 的知识源：
- tables: 每张表的中文名、描述、字段语义、同义词
- linker 据此把自然语言片段映射到 表.字段，并裁剪出与问句相关的 schema 子集
"""
from sqlalchemy import text as sql_text

# 表级语义目录（8 张业务表）
TABLE_CATALOG = {
    "orders": {
        "zh": "订单表",
        "description": "记录每笔交易订单：客户、门店、下单时间、订单总金额、支付方式与订单状态",
        "synonyms": ["订单", "成交", "交易", "购买记录", "订单量", "订单数", "业绩"],
        "columns": {
            "order_id": {"zh": "订单ID", "synonyms": ["订单编号"]},
            "customer_id": {"zh": "客户ID", "synonyms": ["客户编号", "买家"]},
            "shop_id": {"zh": "门店ID", "synonyms": ["门店编号", "店铺"]},
            "order_date": {"zh": "下单时间", "synonyms": ["下单日期", "成交时间", "购买时间", "日期", "时间"]},
            "total_amount": {"zh": "订单总金额", "synonyms": ["销售额", "营业额", "成交金额", "实收金额", "金额", "卖了多少钱", "营业收入", "营收", "销售总额"]},
            "pay_method": {"zh": "支付方式", "synonyms": ["微信", "支付宝", "现金", "银行卡", "付款方式"]},
            "status": {"zh": "订单状态", "synonyms": ["已完成", "待付款", "已退款", "订单状态"]},
        },
    },
    "order_items": {
        "zh": "订单明细表",
        "description": "订单内每行商品明细：商品、数量、单价、小计（计算销量需关联本表）",
        "synonyms": ["明细", "销量", "卖出多少", "售出", "件数", "卖了多少件"],
        "columns": {
            "item_id": {"zh": "明细ID", "synonyms": []},
            "order_id": {"zh": "订单ID", "synonyms": ["订单编号"]},
            "product_id": {"zh": "商品ID", "synonyms": ["商品编号"]},
            "quantity": {"zh": "购买数量", "synonyms": ["销量", "数量", "件数", "卖出多少件"]},
            "unit_price": {"zh": "成交单价", "synonyms": ["单价", "售价"]},
            "subtotal": {"zh": "小计金额", "synonyms": ["小计", "明细金额"]},
        },
    },
    "products": {
        "zh": "商品表",
        "description": "商品主数据：名称、所属类目、售价、成本价、供应商与在售状态",
        "synonyms": ["商品", "货品", "产品", "茶叶", "货物"],
        "columns": {
            "product_id": {"zh": "商品ID", "synonyms": ["商品编号"]},
            "product_name": {"zh": "商品名称", "synonyms": ["品名", "商品名", "茶名"]},
            "category_id": {"zh": "类目ID", "synonyms": ["类目", "品类", "分类"]},
            "unit_price": {"zh": "标价单价", "synonyms": ["价格", "多少钱", "售价", "标价", "定价"]},
            "cost_price": {"zh": "成本价", "synonyms": ["成本", "进价"]},
            "supplier": {"zh": "供应商", "synonyms": ["供货商", "厂家"]},
            "status": {"zh": "在售状态", "synonyms": ["在售", "下架", "上架"]},
        },
    },
    "categories": {
        "zh": "类目表",
        "description": "商品类目：绿茶、红茶、乌龙茶、白茶、黑茶、花茶",
        "synonyms": ["类目", "品类", "分类", "茶类"],
        "columns": {
            "category_id": {"zh": "类目ID", "synonyms": []},
            "category_name": {"zh": "类目名称", "synonyms": ["类目名", "品类名"]},
            "parent_id": {"zh": "父类目ID", "synonyms": []},
        },
    },
    "customers": {
        "zh": "客户表",
        "description": "客户档案：姓名、会员/散客类型、手机号、城市、注册日期",
        "synonyms": ["客户", "会员", "散客", "顾客", "买家"],
        "columns": {
            "customer_id": {"zh": "客户ID", "synonyms": ["客户编号"]},
            "customer_name": {"zh": "客户姓名", "synonyms": ["姓名", "客户名"]},
            "customer_type": {"zh": "客户类型", "synonyms": ["会员", "散客", "会员类型"]},
            "phone": {"zh": "手机号", "synonyms": ["电话", "联系方式"]},
            "register_date": {"zh": "注册日期", "synonyms": ["注册时间", "入会时间", "新增会员"]},
        },
    },
    "shops": {
        "zh": "门店表",
        "description": "门店档案：名称、所在城市、开业日期、营业面积",
        "synonyms": ["门店", "店铺", "分店", "店面"],
        "columns": {
            "shop_id": {"zh": "门店ID", "synonyms": ["门店编号"]},
            "shop_name": {"zh": "门店名称", "synonyms": ["店名", "门店名"]},
            "city": {"zh": "所在城市", "synonyms": ["城市"]},
            "open_date": {"zh": "开业日期", "synonyms": ["开业时间"]},
            "area_m2": {"zh": "营业面积", "synonyms": ["面积"]},
        },
    },
    "inventory": {
        "zh": "库存表",
        "description": "门店×商品粒度的实时库存：现有库存量与安全库存阈值",
        "synonyms": ["库存", "存货", "现货", "存量", "还有多少货", "剩多少"],
        "columns": {
            "inventory_id": {"zh": "库存记录ID", "synonyms": []},
            "product_id": {"zh": "商品ID", "synonyms": ["商品编号"]},
            "shop_id": {"zh": "门店ID", "synonyms": ["门店编号"]},
            "stock_qty": {"zh": "现有库存量", "synonyms": ["库存量", "库存数量", "存货量", "还剩多少"]},
            "safety_stock": {"zh": "安全库存阈值", "synonyms": ["安全库存", "预警阈值", "低于"]},
            "update_date": {"zh": "盘点日期", "synonyms": ["更新日期"]},
        },
    },
    "stock_records": {
        "zh": "出入库流水表",
        "description": "入库/出库/盘点流水记录：商品、门店、变动数量、类型与时间",
        "synonyms": ["入库", "出库", "流水", "盘点", "补货记录"],
        "columns": {
            "record_id": {"zh": "流水ID", "synonyms": []},
            "product_id": {"zh": "商品ID", "synonyms": ["商品编号"]},
            "shop_id": {"zh": "门店ID", "synonyms": ["门店编号"]},
            "change_qty": {"zh": "变动数量", "synonyms": ["变动", "增减"]},
            "record_type": {"zh": "流水类型", "synonyms": ["入库", "出库", "盘点"]},
            "record_date": {"zh": "流水时间", "synonyms": ["日期", "时间"]},
        },
    },
}

# 业务别名 → 数据库规范值（查询条件归一化）
ALIAS_MAPS = {
    "products": {
        "龙井": "西湖龙井", "龙井茶": "西湖龙井",
        "铁观音": "安溪铁观音", "普洱": "云南普洱", "普洱茶": "云南普洱",
        "正山小种": "正山小种", "茉莉花茶": "茉莉花茶",
    },
    "categories": {},
    "shops": {"旗舰店": "西湖旗舰店", "宝龙店": "滨江宝龙店", "万象城店": "余杭万象城店"},
}

# 槽位 → 过滤条件用字段
DIMENSION_TABLE = {
    "product": ("products", "product_name"),
    "shop": ("shops", "shop_name"),
    "customer": ("customers", "customer_type"),
    "category": ("categories", "category_name"),
    "pay_method": ("orders", "pay_method"),
}


def load_entity_dict(session) -> dict:
    """从数据库加载实体词典（商品/类目/门店名 + 别名表），供槽位抽取与 Schema Linking 使用"""
    products = [r[0] for r in session.execute(sql_text("SELECT product_name FROM products"))]
    categories = [r[0] for r in session.execute(sql_text("SELECT category_name FROM categories"))]
    shops = [r[0] for r in session.execute(sql_text("SELECT shop_name FROM shops"))]
    return {
        "products": products,
        "categories": categories,
        "shops": shops,
        "product_alias": ALIAS_MAPS["products"],
        "category_alias": ALIAS_MAPS["categories"],
        "shop_alias": ALIAS_MAPS["shops"],
    }
