-- clean_orders

CREATE TABLE IF NOT EXISTS clean_orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT,
    order_status TEXT NOT NULL,

    order_purchase_timestamp TIMESTAMP NOT NULL,
    order_approved_at TIMESTAMP,
    order_delivered_carrier_date TIMESTAMP,
    order_delivered_customer_date TIMESTAMP,
    order_estimated_delivery_date TIMESTAMP,

    order_year SMALLINT NOT NULL,
    order_month SMALLINT NOT NULL,

    batch_year SMALLINT NOT NULL,
    batch_month SMALLINT NOT NULL,

    source_s3_key TEXT NOT NULL,
    stg_loaded_at TIMESTAMPTZ NOT NULL,
    cleaned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT clean_orders_month_check
        CHECK (order_month BETWEEN 1 AND 12),

    CONSTRAINT clean_orders_purchase_partition_check
        CHECK (
            EXTRACT(YEAR FROM order_purchase_timestamp)::SMALLINT = order_year
            AND EXTRACT(MONTH FROM order_purchase_timestamp)::SMALLINT=order_month
        )
);

CREATE TABLE IF NOT EXISTS quarantine_orders (
    quarantine_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    order_id TEXT,
    customer_id TEXT,
    order_status TEXT,

    order_purchase_timestamp_raw TEXT,
    order_approved_at_raw TEXT,
    order_delivered_carrier_date_raw TEXT,
    order_delivered_customer_date_raw TEXT,
    order_estimated_delivery_date_raw TEXT,

    order_year SMALLINT,
    order_month SMALLINT,

    batch_year SMALLINT NOT NULL,
    batch_month SMALLINT NOT NULL,

    source_s3_key TEXT NOT NULL,
    stg_loaded_at TIMESTAMPTZ NOT NULL,

    rejection_reason TEXT NOT NULL,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (
        batch_year,
        batch_month,
        order_id,
        rejection_reason
    )
);