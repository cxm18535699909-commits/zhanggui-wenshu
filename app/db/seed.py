"""模拟数据生成 · 茶叶零售连锁「掌柜茶铺」

固定随机种子保证可复现；订单覆盖近 180 天，支撑趋势/同比类查询。
python -m app.cli build-db
"""
import random
from datetime import datetime, timedelta

from sqlalchemy import select, func

from app.config import load_config
from app.db.schema import (
    Base, Category, Customer, Inventory, Order, OrderItem, Product,
    Shop, StockRecord, ALL_TABLES, get_engine, get_session_factory,
)

rng = random.Random(42)

SHOPS = [
    ("西湖旗舰店", "杭州", 420), ("滨江宝龙店", "杭州", 260), ("余杭万象城店", "杭州", 300),
]
CATEGORIES = ["绿茶", "红茶", "乌龙茶", "白茶", "黑茶", "花茶"]
PRODUCTS = {
    "绿茶": ["西湖龙井", "碧螺春", "黄山毛峰", "安吉白茶", "恩施玉露"],
    "红茶": ["金骏眉", "正山小种", "祁门红茶", "滇红", "英德红茶"],
    "乌龙茶": ["安溪铁观音", "大红袍", "凤凰单丛", "冻顶乌龙", "漳平水仙"],
    "白茶": ["白毫银针", "白牡丹", "寿眉", "贡眉"],
    "黑茶": ["云南普洱", "安化黑茶", "六堡茶", "雅安藏茶"],
    "花茶": ["茉莉花茶", "桂花乌龙", "菊花枸杞茶", "玫瑰红茶"],
}
SUPPLIERS = ["杭州茶厂", "武夷山茶业", "安溪供销社", "云南勐海茶业", "福鼎白茶集团"]
SURNAMES = "王李张刘陈杨黄赵周吴徐孙马朱胡郭何林罗高郑梁谢宋唐许韩冯邓曹彭曾"
GIVEN = ["芳", "强", "伟", "敏", "静", "磊", "军", "洋", "勇", "艳", "杰", "涛", "明", "超", "秀英", "建国", "桂英", "玉梅"]
PAY_METHODS = ["微信支付", "支付宝", "现金", "银行卡"]
PAY_WEIGHTS = [0.45, 0.35, 0.08, 0.12]


def build_db(cfg=None) -> dict:
    cfg = cfg or load_config()
    engine = get_engine(cfg)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = get_session_factory(cfg)()

    today = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

    # ---- 门店 / 类目 / 商品 ----
    shops = [Shop(shop_name=n, city=c, open_date=today - timedelta(days=900 - i * 90), area_m2=a)
             for i, (n, c, a) in enumerate(SHOPS)]
    cats = [Category(category_name=n, parent_id=None) for n in CATEGORIES]
    session.add_all(shops + cats)
    session.flush()

    products = []
    for cat in cats:
        for i, name in enumerate(PRODUCTS[cat.category_name]):
            cost = round(rng.uniform(30, 400), 2)
            products.append(Product(
                product_name=name, category_id=cat.category_id,
                unit_price=round(cost * rng.uniform(1.4, 2.2), 2),
                cost_price=cost, supplier=rng.choice(SUPPLIERS),
                status="在售" if rng.random() > 0.08 else "下架",
            ))
    session.add_all(products)
    session.flush()

    # ---- 客户 ----
    customers = []
    for i in range(120):
        name = rng.choice(SURNAMES) + rng.choice(GIVEN) + (str(rng.randint(1, 99)) if rng.random() < 0.3 else "")
        customers.append(Customer(
            customer_name=name,
            customer_type="会员" if rng.random() < 0.55 else "散客",
            phone=f"1{rng.choice('3589')}{rng.randint(1000000000, 9999999999)}"[:11],
            city="杭州", register_date=(today - timedelta(days=rng.randint(30, 800))).date(),
        ))
    session.add_all(customers)
    session.flush()

    # ---- 订单 + 明细（近 180 天，带周末/节假日高峰与增长趋势）----
    active_products = [p for p in products if p.status == "在售"]
    n_orders = n_items = 0
    for d in range(180, -1, -1):
        day = today - timedelta(days=d)
        # 增长趋势 + 周末高峰
        base = 18 + (180 - d) // 9
        if day.weekday() >= 5:
            base = int(base * 1.5)
        for _ in range(base):
            cust = rng.choice(customers)
            shop = rng.choice(shops)
            n_lines = rng.randint(1, 4)
            total = 0.0
            items = []
            for _ in range(n_lines):
                p = rng.choice(active_products)
                qty = rng.randint(1, 5)
                price = float(p.unit_price)
                subtotal = round(price * qty, 2)
                total += subtotal
                items.append((p, qty, price, subtotal))
            order = Order(
                customer_id=cust.customer_id, shop_id=shop.shop_id,
                order_date=day - timedelta(minutes=rng.randint(0, 600)),
                total_amount=round(total, 2),
                pay_method=rng.choices(PAY_METHODS, PAY_WEIGHTS)[0],
                status=rng.choices(["已完成", "待付款", "已退款"], [0.92, 0.05, 0.03])[0],
            )
            session.add(order)
            session.flush()
            for p, qty, price, sub in items:
                session.add(OrderItem(order_id=order.order_id, product_id=p.product_id,
                                      quantity=qty, unit_price=price, subtotal=sub))
                n_items += 1
            n_orders += 1

    # ---- 库存：约 12% 低于安全库存（支撑库存预警查询）----
    inventories, records = [], []
    for p in products:
        for shop in shops:
            stock = rng.randint(20, 400)
            safety = rng.randint(30, 80)
            if rng.random() < 0.12:  # 人为制造低库存
                stock = rng.randint(0, safety - 1)
            inventories.append(Inventory(product_id=p.product_id, shop_id=shop.shop_id,
                                         stock_qty=stock, safety_stock=safety,
                                         update_date=today.date()))
            for k in range(rng.randint(1, 3)):
                records.append(StockRecord(
                    product_id=p.product_id, shop_id=shop.shop_id,
                    change_qty=rng.choice([100, 200, 300, -50, -80]),
                    record_type=rng.choice(["入库", "出库", "盘点"]),
                    record_date=today - timedelta(days=rng.randint(1, 60)),
                ))
    session.add_all(inventories + records)
    session.commit()

    stats = {
        "shops": session.query(func.count(Shop.shop_id)).scalar(),
        "categories": session.query(func.count(Category.category_id)).scalar(),
        "products": session.query(func.count(Product.product_id)).scalar(),
        "customers": session.query(func.count(Customer.customer_id)).scalar(),
        "orders": n_orders,
        "order_items": n_items,
        "inventory": session.query(func.count(Inventory.inventory_id)).scalar(),
        "stock_records": session.query(func.count(StockRecord.record_id)).scalar(),
    }
    session.close()
    return stats


if __name__ == "__main__":
    from pprint import pprint
    pprint(build_db())
