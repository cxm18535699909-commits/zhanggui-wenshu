"""SQLAlchemy ORM 模型 · 8 张业务表

SQLite / MySQL 双后端兼容：同一套 ORM 模型，
- sqlite: sqlite:///data/db/zhanggui.db
- mysql:  mysql+pymysql://user:pwd@host:3306/zhanggui_wenshu
"""
from datetime import datetime

from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import load_config

Base = declarative_base()


class Shop(Base):
    __tablename__ = "shops"
    shop_id = Column(Integer, primary_key=True, autoincrement=True)
    shop_name = Column(String(64), nullable=False)
    city = Column(String(32), nullable=False)
    open_date = Column(Date, nullable=False)
    area_m2 = Column(Integer)


class Category(Base):
    __tablename__ = "categories"
    category_id = Column(Integer, primary_key=True, autoincrement=True)
    category_name = Column(String(32), nullable=False)
    parent_id = Column(Integer, nullable=True)


class Product(Base):
    __tablename__ = "products"
    product_id = Column(Integer, primary_key=True, autoincrement=True)
    product_name = Column(String(64), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.category_id"), nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    cost_price = Column(Numeric(10, 2), nullable=False)
    supplier = Column(String(64))
    status = Column(String(16), nullable=False, default="在售", index=True)


class Customer(Base):
    __tablename__ = "customers"
    customer_id = Column(Integer, primary_key=True, autoincrement=True)
    customer_name = Column(String(64), nullable=False)
    customer_type = Column(String(16), nullable=False, default="散客", index=True)
    phone = Column(String(20))
    city = Column(String(32))
    register_date = Column(Date)


class Order(Base):
    __tablename__ = "orders"
    order_id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.shop_id"), nullable=False)
    order_date = Column(DateTime, nullable=False, index=True)
    total_amount = Column(Numeric(12, 2), nullable=False)
    pay_method = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, index=True)


class OrderItem(Base):
    __tablename__ = "order_items"
    item_id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.order_id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    subtotal = Column(Numeric(12, 2), nullable=False)


class Inventory(Base):
    __tablename__ = "inventory"
    inventory_id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.shop_id"), nullable=False)
    stock_qty = Column(Integer, nullable=False, default=0)
    safety_stock = Column(Integer, nullable=False, default=0)
    update_date = Column(Date)
    __table_args__ = (Index("uk_product_shop", "product_id", "shop_id", unique=True),)


class StockRecord(Base):
    __tablename__ = "stock_records"
    record_id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.shop_id"), nullable=False)
    change_qty = Column(Integer, nullable=False)
    record_type = Column(String(16), nullable=False)
    record_date = Column(DateTime, nullable=False, index=True)


ALL_TABLES = [Shop, Category, Product, Customer, Order, OrderItem, Inventory, StockRecord]


def get_engine(cfg=None):
    """根据配置创建引擎（sqlite / mysql 双后端）"""
    cfg = cfg or load_config()
    if cfg.db_backend == "mysql":
        m = cfg.mysql_conf
        url = (
            f"mysql+pymysql://{m['user']}:{m['password']}@{m['host']}:{m['port']}"
            f"/{m['database']}?charset={m.get('charset', 'utf8mb4')}"
        )
        return create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    return create_engine(f"sqlite:///{cfg.sqlite_path}")


def get_session_factory(cfg=None):
    engine = get_engine(cfg)
    return sessionmaker(bind=engine, expire_on_commit=False)
