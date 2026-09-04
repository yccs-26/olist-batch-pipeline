WITH normalized AS (
    SELECT
        s.*,
        
        NULLIF(TRIM(s.order_id), '') AS normalized_order_id,
        NULLIF(TRIM(s.order_status), '') AS normalized_order_status,

        NULLIF(TRIM(s.order_purchase_timestamp), '') AS purchase_timestamp_text,
        NULLIF(TRIM(s.order_approved_at), '') AS approved_timestamp_text,
        NULLIF(TRIM(s.order_delivered_carrier_date), '') AS delivered_carrier_timestamp_text,
        NULLIF(TRIM(s.order_delivered_customer_date), '') AS delivered_customer_timestamp_text,
        NULLIF(TRIM(s.order_estimated_delivery_date), '') AS estimated_timestamp_text
    
    FROM stg_orders AS s
    WHERE s.batch_year = 2017
     AND s.batch_month = 2
),

parsed AS (
    SELECT
        n.*,

        try_parse_timestamp(n.purchase_timestamp_text) AS parsed_purchase_timestamp,
        try_parse_timestamp(n.approved_timestamp_text) AS parsed_approved_timestamp,
        try_parse_timestamp(n.delivered_carrier_timestamp_text) AS parsed_delivered_carrier_timestamp,
        try_parse_timestamp(n.delivered_customer_timestamp_text) AS parsed_delivered_customer_timestamp,
        try_parse_timestamp(n.estimated_timestamp_text) AS parsed_estimated_timestamp

    FROM normalized AS n
),
-- normalized 컬럼 : 원본 값 존재 여부 
-- parsed 컬럼 : 변환 성공 여부
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
                p.delivered_carrier_timestamp_text IS NOT NULL
                AND p.parsed_delivered_carrier_timestamp IS NULL
          )
          OR (
                p.delivered_customer_timestamp_text IS NOT NULL
                AND p.parsed_delivered_customer_timestamp IS NULL
          )
          OR (
            p.estimated_timestamp_text IS NOT NULL
            AND p.parsed_estimated_timestamp IS NULL
          )
            THEN 'OPTIONAL_TIMESTAMP_INVALID'
         
         WHEN EXTRACT(YEAR FROM p.parsed_purchase_timestamp)::SMALLINT IS DISTINCT FROM p.order_year::SMALLINT
          OR EXTRACT(MONTH FROM p.parsed_purchase_timestamp)::SMALLINT IS DISTINCT FROM p.order_month::SMALLINT
            THEN 'ORDER_PARTITION_MISMATCH'
         
         ELSE NULL  
        END AS rejection_reason

    FROM parsed AS p
)

SELECT
    COUNT(*) AS source_raws,
    COUNT(*) FILTER (
        WHERE rejection_reason IS NULL
    ) AS valid_rows,
    COUNT(*) FILTER (
        WHERE rejection_reason IS NOT NULL
    ) AS invalid_rows,
    COUNT(*) FILTER (
        WHERE rejection_reason IS NULL
    )
    + 
    COUNT(*) FILTER (
        WHERE rejection_reason IS NOT NULL
    ) AS classified_rows
FROM classified;