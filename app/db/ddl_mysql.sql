-- 掌柜问数 · MySQL 8.0 建表 DDL（与 SQLAlchemy 模型一一对应）
-- SQLite 后端使用 app/db/schema.py 中的 ORM 自动建表；本文件用于 MySQL 生产部署。
CREATE DATABASE IF NOT EXISTS zhanggui_wenshu DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE zhanggui_wenshu;

-- 1. 门店表
CREATE TABLE IF NOT EXISTS shops (
    shop_id     INT AUTO_INCREMENT PRIMARY KEY,
    shop_name   VARCHAR(64) NOT NULL,
    city        VARCHAR(32) NOT NULL,
    open_date   DATE NOT NULL,
    area_m2     INT
) ENGINE=InnoDB;

-- 2. 商品类目表
CREATE TABLE IF NOT EXISTS categories (
    category_id   INT AUTO_INCREMENT PRIMARY KEY,
    category_name VARCHAR(32) NOT NULL,
    parent_id     INT NULL
) ENGINE=InnoDB;

-- 3. 商品表
CREATE TABLE IF NOT EXISTS products (
    product_id    INT AUTO_INCREMENT PRIMARY KEY,
    product_name  VARCHAR(64) NOT NULL,
    category_id   INT NOT NULL,
    unit_price    DECIMAL(10,2) NOT NULL,
    cost_price    DECIMAL(10,2) NOT NULL,
    supplier      VARCHAR(64),
    status        VARCHAR(16) NOT NULL DEFAULT '在售',
    FOREIGN KEY (category_id) REFERENCES categories(category_id),
    INDEX idx_products_name (product_name),
    INDEX idx_products_status (status)
) ENGINE=InnoDB;

-- 4. 客户表
CREATE TABLE IF NOT EXISTS customers (
    customer_id   INT AUTO_INCREMENT PRIMARY KEY,
    customer_name VARCHAR(64) NOT NULL,
    customer_type VARCHAR(16) NOT NULL DEFAULT '散客',  -- 会员 / 散客
    phone         VARCHAR(20),
    city          VARCHAR(32),
    register_date DATE
) ENGINE=InnoDB;

-- 5. 订单表
CREATE TABLE IF NOT EXISTS orders (
    order_id     INT AUTO_INCREMENT PRIMARY KEY,
    customer_id  INT NOT NULL,
    shop_id      INT NOT NULL,
    order_date   DATETIME NOT NULL,
    total_amount DECIMAL(12,2) NOT NULL,
    pay_method   VARCHAR(16) NOT NULL,   -- 微信支付/支付宝/现金/银行卡
    status       VARCHAR(16) NOT NULL,   -- 已完成/待付款/已退款
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
    FOREIGN KEY (shop_id) REFERENCES shops(shop_id),
    INDEX idx_orders_date (order_date),
    INDEX idx_orders_status (status)
) ENGINE=InnoDB;

-- 6. 订单明细表
CREATE TABLE IF NOT EXISTS order_items (
    item_id    INT AUTO_INCREMENT PRIMARY KEY,
    order_id   INT NOT NULL,
    product_id INT NOT NULL,
    quantity   INT NOT NULL,
    unit_price DECIMAL(10,2) NOT NULL,
    subtotal   DECIMAL(12,2) NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id),
    INDEX idx_items_order (order_id),
    INDEX idx_items_product (product_id)
) ENGINE=InnoDB;

-- 7. 库存表（门店×商品粒度）
CREATE TABLE IF NOT EXISTS inventory (
    inventory_id INT AUTO_INCREMENT PRIMARY KEY,
    product_id   INT NOT NULL,
    shop_id      INT NOT NULL,
    stock_qty    INT NOT NULL DEFAULT 0,
    safety_stock INT NOT NULL DEFAULT 0,
    update_date  DATE,
    FOREIGN KEY (product_id) REFERENCES products(product_id),
    FOREIGN KEY (shop_id) REFERENCES shops(shop_id),
    UNIQUE KEY uk_product_shop (product_id, shop_id)
) ENGINE=InnoDB;

-- 8. 出入库流水表
CREATE TABLE IF NOT EXISTS stock_records (
    record_id   INT AUTO_INCREMENT PRIMARY KEY,
    product_id  INT NOT NULL,
    shop_id     INT NOT NULL,
    change_qty  INT NOT NULL,           -- 正数入库 / 负数出库
    record_type VARCHAR(16) NOT NULL,   -- 入库/出库/盘点
    record_date DATETIME NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(product_id),
    FOREIGN KEY (shop_id) REFERENCES shops(shop_id),
    INDEX idx_records_date (record_date)
) ENGINE=InnoDB;
