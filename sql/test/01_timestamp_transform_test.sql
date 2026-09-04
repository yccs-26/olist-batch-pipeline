WITH purchase_candidates AS (
    SELECT
        order_id,
        order_purchase_timestamp,
        CASE
            WHEN TRIM(order_purchase_timestamp)
                ~'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$'
            THEN TRIM(order_purchase_timestamp)
        END AS timestamp_text
    FROM stg_orders
)
SELECT
    COUNT(*) FILTER (
        WHERE timestamp_text IS NULL
    ) AS missing_or_format_invalid_count,
    COUNT(*) FILTER (
        WHERE timestamp_text IS NOT NULL
         AND (
            SUBSTRING(timestamp_text FROM 6 FOR 2)::INT NOT BETWEEN 1 AND 12
            OR SUBSTRING(timestamp_text FROM 9 FOR 2)::INT NOT BETWEEN 1 AND 31
            OR SUBSTRING(timestamp_text FROM 12 FOR 2)::INT NOT BETWEEN 0 AND 23
            OR SUBSTRING(timestamp_text FROM 15 FOR 2)::INT NOT BETWEEN 0 AND 59
            OR SUBSTRING(timestamp_text FROM 18 FOR 2)::INT NOT BETWEEN 0 AND 59
        )
    ) AS range_invalid_count
FROM purchase_candidates;