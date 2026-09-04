WITH stg_counts AS (
    SELECT
        batch_year,
        batch_month,
        COUNT(*) as staging_rows
    FROM stg_orders
    GROUP BY
        batch_year,
        batch_month
),

clean_counts AS (
    SELECT
        batch_year,
        batch_month,
        COUNT(*) AS clean_rows
    FROM clean_orders
    GROUP BY
        batch_year,
        batch_month
),

quarantine_counts AS (
    SELECT
        batch_year,
        batch_month,
        COUNT(*) AS quarantine_rows
    FROM quarantine_orders
    GROUP BY
         batch_year,
         batch_month
)

SELECT
    s.batch_year,
    s.batch_month,
    s.staging_rows,
    COALESCE(c.clean_rows, 0) AS clean_rows,
    COALESCE(q.quarantine_rows, 0) AS quarantine_rows,
    COALESCE(c.clean_rows, 0) + COALESCE(q.quarantine_rows, 0) AS classified_rows,
    s.staging_rows - (
        COALESCE(c.clean_rows, 0) + COALESCE(q.quarantine_rows, 0)
    ) AS row_difference
FROM stg_counts AS s
LEFT JOIN clean_counts as c
    ON c.batch_year = s.batch_year AND c.batch_month = s.batch_month
LEFT JOIN quarantine_counts as q
    ON q.batch_year = s.batch_year AND q.batch_month = s.batch_month
ORDER BY
    s.batch_year,
    s.batch_month;