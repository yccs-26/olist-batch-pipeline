import boto3
import logging
import os
import psycopg

from args_parse import parse_args
from dotenv import load_dotenv
from botocore.exceptions import (
    ConnectTimeoutError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)


# logging 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# S3 다운로드 함수에서 재시도할 예외 목록
S3_RETRY_EXCEPTIONS = (
    EndpointConnectionError,
    ConnectTimeoutError,
    ReadTimeoutError,
    ConnectionClosedError,
)

# DB 재시도 예외 목록
DB_RETRY_EXCEPTIONS = (
    psycopg.OperationalError,
    psycopg.InterfaceError,
)

# SQL ----------------------------------------------------------

create_temp_sql = """
CREATE TEMP TABLE tmp_orders_raw (
    order_id TEXT,
    customer_id TEXT,
    order_status TEXT,
    order_purchase_timestamp TEXT,
    order_approved_at TEXT,
    order_delivered_carrier_date TEXT,
    order_delivered_customer_date TEXT,
    order_estimated_delivery_date TEXT,
    order_year SMALLINT,
    order_month CHAR(2)
) ON COMMIT DROP
"""

copy_sql = """
COPY tmp_orders_raw (
    order_id,
    customer_id,
    order_status,
    order_purchase_timestamp,
    order_approved_at,
    order_delivered_carrier_date,
    order_delivered_customer_date,
    order_estimated_delivery_date,
    order_year,
    order_month
)
FROM STDIN
WITH (
    FORMAT CSV,
    HEADER TRUE
)
"""

# batch_year, batch_month 자주 사용될 변수
delete_sql = """
DELETE FROM stg_orders
WHERE batch_year = %s
    AND batch_month = %s
"""

insert_sql = """
INSERT INTO stg_orders (
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
    source_s3_key
) 
SELECT 
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
    %s,
    %s,
    %s
FROM tmp_orders_raw 
"""

# ----------------------------------------------------------------


def build_s3_key(year: int, month: int) -> str:
    return (
        f"raw/orders/year={year}/month={month:02d}" 
        f"/orders_{year}_{month:02d}.csv"
    )

def validate_env() -> tuple[dict, str, str]:
    required_env_names = [
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "S3_BUCKET",
        "AWS_REGION",
    ]

    missing_env_names = [
        env_name
        for env_name in required_env_names
        if not os.getenv(env_name)
    ]

    if missing_env_names:
        missing_names = ",".join(missing_env_names)

        raise RuntimeError(
            f"필수 환경 변수가 없습니다: {missing_names}"
        )

    db_params = {
        "host": os.environ["POSTGRES_HOST"],
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
        "port": os.environ["POSTGRES_PORT"],
    }

    bucket = os.environ["S3_BUCKET"]
    aws_region = os.environ["AWS_REGION"]

    return db_params, bucket, aws_region


@retry(
    retry=retry_if_exception_type(S3_RETRY_EXCEPTIONS),
    wait=wait_random_exponential(multiplier=1, max=16),
    stop=stop_after_attempt(4),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def fetch_s3_csv(s3_client, bucket: str, s3_key: str,) -> str:

    logger.info(
                "S3 다운로드 시작 | bucket=%s | s3_key=%s",
                bucket,
                s3_key
            )
    
    response = s3_client.get_object(
        Bucket=bucket,
        Key=s3_key,
    )


    csv_text = response["Body"].read().decode("utf-8")

    logger.info(
        "S3 다운로드 완료 | content_length=%s | etag=%s",
        response["ContentLength"],
        response["ETag"]
    )

    return csv_text


@retry(
    retry=retry_if_exception_type(DB_RETRY_EXCEPTIONS),
    wait=wait_random_exponential(multiplier=1, max=16),
    stop=stop_after_attempt(4),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def load_to_staging(
        db_params: dict,
        csv_text: str,
        year: int,
        month: int,
        s3_key: str,
) -> int:
    logger.info("DB 트랜잭션 시작 | batch_year=%s | batch_month=%s",
                year,
                month,
    )

    with psycopg.connect(**db_params) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(create_temp_sql)

                logger.info(
                    "temp table 생성 완료 | table=temp_orders_raw"
                )

                with cur.copy(copy_sql) as copy:
                    copy.write(csv_text)

                logger.info("COPY 완료")

                cur.execute(delete_sql, (year, month))
                deleted_rows = cur.rowcount

                logger.info(
                    "staging DELETE 완료 | deleted_rows=%s",
                    deleted_rows,
                )

                cur.execute(insert_sql, (year, month, s3_key))
                inserted_rows = cur.rowcount

                logger.info(
                    "staging INSERT 완료 | inserted_rows=%s",
                    inserted_rows,
                )
    logger.info(
        "DB transaction 완료 | batch_year=%s | batch_month=%s",
        year,
        month,
    )

    return inserted_rows

def main() -> None:

    # args -> parsing month, year ----------------------------------
    args = parse_args()

    # args.batch_date
    year = args.year
    month = args.month


    # env ----------------------------------------------------------

    load_dotenv()

    db_params, bucket, aws_region = validate_env()

    s3 = boto3.client("s3", region_name=aws_region)
    s3_key = build_s3_key(year=year, month=month)

    logger.info(
        "배치 시작 | batch_year=%s | s3_key=%s",
        year,
        s3_key,
    )

    try:
        csv_text = fetch_s3_csv(s3_client=s3, bucket=bucket, s3_key=s3_key)

        inserted_rows = load_to_staging(
            db_params=db_params,
            csv_text=csv_text,
            year=year,
            month=month,
            s3_key=s3_key,
        )

        logger.info(
            "배치 성공 | batch_date=%s | inserted_rows=%s",
            args.batch_date,
            inserted_rows,
        )
        
    except Exception:
        logger.exception(
            "배치 실패 | batch_date=%s | s3_key=%s",
            args.batch_date,
            s3_key,
        )
        raise

if __name__ == "__main__":
    main()