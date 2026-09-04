BEGIN;

DELETE FROM clean_orders
WHERE batch_year = 2017
    AND batch_month = 2;

DELETE FROM quarantine_orders
WHERE batch_year = 2017
    AND batch_month = 2;

CREATE TEMP TABLE tmp_classified_orders
ON COMMIT DROP
AS 
WITH normalized AS (
    SELECT
        s.*,

        NULLIF(TRIM(s.order_id), '') AS normalized_order_id,
        NULLIF(TRIM(s.order_status), '') AS normalized_order_status,

        NULLIF(TRIM(s.order_purchase_timestamp), '')
            AS purchase_timestamp_text,

        NULLIF(TRIM(s.order_approved_at), '')
            AS approved_timestamp_text,

        NULLIF(TRIM(s.order_delivered_carrier_date), '')
            AS carrier_timestamp_text,

        NULLIF(TRIM(s.order_delivered_customer_date), '')
            AS delivered_timestamp_text,

        NULLIF(TRIM(s.order_estimated_delivery_date), '')
            AS estimated_timestamp_text
    FROM stg_orders AS s
    WHERE s.batch_year = 2017
      AND s.batch_month = 2
),

parsed AS (
    SELECT
        n.*,

        try_parse_timestamp(n.purchase_timestamp_text)
            AS parsed_purchase_timestamp,

        try_parse_timestamp(n.approved_timestamp_text)
            AS parsed_approved_timestamp,

        try_parse_timestamp(n.carrier_timestamp_text)
            AS parsed_delivered_carrier_timestamp,

        try_parse_timestamp(n.delivered_timestamp_text)
            AS parsed_delivered_customer_timestamp,

        try_parse_timestamp(n.estimated_timestamp_text)
            AS parsed_estimated_delivery_timestamp
    FROM normalized AS n
),

classified AS (
    SELECT
        p.*,

        CASE
            WHEN p.normalized_order_id IS NULL
                THEN 'ORDER_ID_REQUIRED'

            WHEN p.normalized_order_status IS NULL
                THEN 'ORDER_STATUS_REQUIRED'

            WHEN p.purchase_timestamp_text IS NULL
                THEN 'PURCHASE_TIMESTAMP_REQUIRED'

            WHEN p.parsed_purchase_timestamp IS NULL
                THEN 'PURCHASE_TIMESTAMP_INVALID'

            WHEN (
                p.approved_timestamp_text IS NOT NULL
                AND p.parsed_approved_timestamp IS NULL
            )
            OR (
                p.carrier_timestamp_text IS NOT NULL
                AND p.parsed_delivered_carrier_timestamp IS NULL
            )
            OR (
                p.delivered_timestamp_text IS NOT NULL
                AND p.parsed_delivered_customer_timestamp IS NULL
            )
            OR (
                p.estimated_timestamp_text IS NOT NULL
                AND p.parsed_estimated_delivery_timestamp IS NULL
            )
                THEN 'OPTIONAL_TIMESTAMP_INVALID'

            WHEN EXTRACT(YEAR FROM p.parsed_purchase_timestamp)::SMALLINT
                    IS DISTINCT FROM p.order_year::SMALLINT
              OR EXTRACT(MONTH FROM p.parsed_purchase_timestamp)::SMALLINT
                    IS DISTINCT FROM p.order_month::SMALLINT
                THEN 'ORDER_PARTITION_MISMATCH'

            ELSE NULL
        END AS rejection_reason
    FROM parsed AS p
)
SELECT 
    normalized_order_id,
    customer_id,
    normalized_order_status,

    order_purchase_timestamp,
    order_approved_at,
    order_delivered_carrier_date,
    order_delivered_customer_date,
    order_estimated_delivery_date,

    parsed_purchase_timestamp,
    parsed_approved_timestamp,
    parsed_delivered_carrier_timestamp,
    parsed_delivered_customer_timestamp,
    parsed_estimated_delivery_timestamp,

    order_year,
    order_month,
    batch_year,
    batch_month,

    source_s3_key,
    loaded_at,
    rejection_reason
FROM classified;

-- INSERT INTO clean_orders
INSERT INTO clean_orders (
    order_id,
    customer_id,
    order_status,
    order_purchase_timestamp,
    order_approved_at,
    order_delivered_carrier_date,
    order_delivered_customer_date,
    order_estimated_delivery_date,
    order_year,
    order_month,
    batch_year,
    batch_month,
    source_s3_key,
    stg_loaded_at
)
SELECT
    normalized_order_id,
    customer_id,
    normalized_order_status,
    parsed_purchase_timestamp,
    parsed_approved_timestamp,
    parsed_delivered_carrier_timestamp,
    parsed_delivered_customer_timestamp,
    parsed_estimated_delivery_timestamp,
    EXTRACT(YEAR FROM parsed_purchase_timestamp)::SMALLINT,
    EXTRACT(MONTH FROM parsed_purchase_timestamp)::SMALLINT,
    batch_year,
    batch_month,
    source_s3_key,
    loaded_at
FROM tmp_classified_orders
WHERE rejection_reason IS NULL;


-- INSERT INTO quaranine_orders
INSERT INTO quarantine_orders (
    order_id,
    customer_id,
    order_status,
    order_purchase_timestamp_raw,
    order_approved_at_raw,
    order_delivered_carrier_date_raw,
    order_delivered_customer_date_raw,
    order_estimated_delivery_date_raw,
    order_year,
    order_month,
    batch_year,
    batch_month,
    source_s3_key,
    stg_loaded_at,
    rejection_reason
)
SELECT
    normalized_order_id,
    customer_id,
    normalized_order_status,
    order_purchase_timestamp,
    order_approved_at,
    order_delivered_carrier_date,
    order_delivered_customer_date,
    order_estimated_delivery_date,
    EXTRACT(YEAR FROM parsed_purchase_timestamp)::SMALLINT,
    EXTRACT(MONTH FROM parsed_purchase_timestamp)::SMALLINT,
    batch_year,
    batch_month,
    source_s3_key,
    loaded_at,
    rejection_reason
FROM tmp_classified_orders
WHERE rejection_reason IS NOT NULL;

-- 검증
DO $$
DECLARE
    source_rows BIGINT;
    clean_rows BIGINT;
    quarantine_rows BIGINT;
BEGIN
    SELECT COUNT(*)
    INTO source_rows
    FROM stg_orders
    WHERE batch_year = 2017
     AND batch_month = 2;

    SELECT COUNT(*)
    INTO clean_rows
    FROM clean_orders
    WHERE batch_year = 2017
     AND batch_month = 2;
    
    SELECT COUNT(*)
    INTO quarantine_rows
    FROM quarantine_orders
    WHERE batch_year = 2017
     AND batch_month = 2;

    IF  source_rows <> clean_rows + quarantine_rows THEN
        RAISE EXCEPTION
            'clean/quarantine row count 불일치 | source_rows=% | clean_rows=% | quarantine_rows=%',
            source_rows,
            clean_rows,
            quarantine_rows;
    END IF;
    
    RAISE NOTICE
        '검증 성공 | source_rows=% | clean_rows=% | quarantine_rows=%',
        source_rows,
        clean_rows,
        quarantine_rows;
END;
$$;


COMMIT;