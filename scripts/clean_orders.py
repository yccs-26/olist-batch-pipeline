import logging
import os
import sys
import time

import psycopg
from dotenv import load_dotenv

from args_parse import parse_args

# logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)

# SQL 
create_tmp_classified_sql = """
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
    WHERE s.batch_year = %s
      AND s.batch_month = %s
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
"""
# 검증용 SQL
count_classified_sql = """
SELECT
    COUNT(*) AS source_rows,
    COUNT(*) FILTER (
        WHERE rejection_reason IS NULL
    ) AS valid_rows,
    COUNT(*) FILTER (
        WHERE rejection_reason IS NOT NULL
    ) AS invalid_rows
FROM tmp_classified_orders
"""

delete_clean_sql = """
DELETE FROM clean_orders
WHERE batch_year = %s
 AND batch_month = %s
"""

delete_quarantine_sql = """
DELETE FROM quarantine_orders
WHERE batch_year =  %s
 AND batch_month = %s
 """

insert_clean_sql = """
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
WHERE rejection_reason IS NULL
"""

insert_quarantine_sql = """
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
    order_year::SMALLINT,
    order_month::SMALLINT,
    batch_year,
    batch_month,
    source_s3_key,
    loaded_at,
    rejection_reason
FROM tmp_classified_orders
WHERE rejection_reason IS NOT NULL
"""

def validate_env() -> dict:
    required_env_names = [
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ]

    missing_env_names = [
        env_name
        for env_name in required_env_names
        if not os.getenv(env_name)
    ]

    if missing_env_names:
        missing_names = ", ".join(missing_env_names)

        raise RuntimeError(
            f"필수 환경 변수 X : {missing_names}"
        )

    return {
        "host": os.environ["POSTGRES_HOST"],
        "port": os.environ["POSTGRES_PORT"],
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
    }

def main() -> None:
    args = parse_args()

    year = args.year
    month = args.month

    load_dotenv()

    db_params = validate_env()

    batch_started_at = time.perf_counter()

    try:
        logger.info(
            "clean 배치 시작 | batch_date=%s",
            args.batch_date
        )

        with psycopg.connect(**db_params) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    logger.info(
                        "clean DB transaction 시작 | batch_year=%s | batch_month=%s",
                        year,
                        month,
                    )

                    cur.execute(
                        create_tmp_classified_sql,
                        (year, month),
                    )

                    logger.info(
                        "temp classified table 생성 완료 | "
                        "table=tmp_classified_orders"
                    )

                    cur.execute(count_classified_sql)

                    source_rows, valid_rows, invalid_rows = cur.fetchone()

                    logger.info(
                        "분류 완료 | source_rows=%s | valid_rows=%s | invalid_rows=%s",
                        source_rows,
                        valid_rows,
                        invalid_rows,
                    )


                    if source_rows != valid_rows + invalid_rows:
                        raise RuntimeError(
                            "분류 행 수 불일치 | "
                            "source_rows=%s | valid_rows=%s | invalid_rows=%s",
                            source_rows,
                            valid_rows,
                            invalid_rows,
                        )

                    cur.execute(delete_clean_sql, (year, month))
                    deleted_clean_rows = cur.rowcount

                    logger.info(
                        "clean 기존 batch DELETE 완료 | deleted_clean_rows=%s",
                        deleted_clean_rows,
                    )

                    cur.execute(delete_quarantine_sql, (year, month))
                    deleted_quarantine_rows = cur.rowcount

                    logger.info(
                        "quarantine 기존 batch DELETE 완료 | deleted_quarantine_rows=%s",
                        deleted_quarantine_rows,
                    )


                    cur.execute(insert_clean_sql)
                    inserted_clean_rows = cur.rowcount

                    logger.info(
                        "clean INSERT 완료 | inserted_clean_rows=%s",
                        inserted_clean_rows,
                    )

                    cur.execute(insert_quarantine_sql)
                    inserted_quarantine_rows = cur.rowcount

                    logger.info(
                        "quarantine INSERT 완료 | inserted_quarantine_rows=%s",
                        inserted_quarantine_rows,
                    )

                    if valid_rows != inserted_clean_rows:
                        raise RuntimeError(
                            "clean 적재 행 수 불일치 | "
                            "valid_rows=%s | inserted_clean_rows=%s",
                            valid_rows,
                            inserted_clean_rows,
                        )

                    if invalid_rows != inserted_quarantine_rows:
                        raise RuntimeError(
                            "quarantine 적재 행 수 불일치 | "
                            "invalid_rows=%s | inserted_quarantine_rows=%s",
                            invalid_rows,
                            inserted_quarantine_rows
                        )

                    logger.info(
                        "clean 적재 검증 성공 | "
                        "source_rows=%s | inserted_clean_rows=%s | "
                        "inserted_quarantine_rows=%s",
                        source_rows,
                        inserted_clean_rows,
                        inserted_quarantine_rows,
                    )


        elapsed_seconds = time.perf_counter() - batch_started_at

        logger.info(
            "clean 배치 성공 | batch_date=%s | elapsed_seconds=%.2f",
            args.batch_date,
            elapsed_seconds,
        )

    except Exception:
        logger.exception(
            "clean 배치 실패 | batch_date=%s",
            args.batch_date
        )
        raise

if __name__ == "__main__":
    main()