-- DROP TABLE IF EXISTS stg_orders;

CREATE TABLE IF NOT EXISTS stg_orders (
    -- csv 컬럼
    order_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    order_status TEXT,
    order_purchase_timestamp TEXT,
    order_approved_at TEXT,
    order_delivered_carrier_date TEXT,
    order_delivered_customer_date TEXT,
    order_estimated_delivery_date TEXT,

    order_year SMALLINT NOT NULL,
    order_month CHAR(2) NOT NULL,

    -- pipeline metadata
    batch_year SMALLINT NOT NULL,
    batch_month SMALLINT NOT NULL,
    source_s3_key TEXT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (order_id)
);