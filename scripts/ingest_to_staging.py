import boto3
import logging
import os
import psycopg

from args_parse import parse_args
from dotenv import load_dotenv


# logging 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# args -> parsing month, year ----------------------------------
args = parse_args()

# args.batch_date
year = args.year
month = args.month


# env ----------------------------------------------------------

load_dotenv()

host = os.getenv("POSTGRES_HOST")
port = os.getenv("POSTGRES_PORT")
db = os.getenv("POSTGRES_DB")
username = os.getenv("POSTGRES_USER")
password = os.getenv("POSTGRES_PASSWORD")


db_params = {
    "host": host,
    "dbname": db,
    "user": username,
    "password": password,
    "port": port,
}

bucket = os.getenv("S3_BUCKET")
aws_region = os.getenv("AWS_REGION")


s3 = boto3.client("s3", region_name=aws_region)

s3_key = (
    f"raw/orders/year={year}/month={month:02d}" 
    f"/orders_{year}_{month:02d}.csv"
)

logger.info(
    "배치 시작 | batch_year=%s | s3_key=%s",
    year,
    s3_key,
)
try:
    response = s3.get_object(
        Bucket=bucket,
        Key=s3_key,
    )


    logger.info(
        "S3 다운로드 시작 | bucket=%s | s3_key=%s",
        bucket,
        s3_key
    )

    csv_text = response["Body"].read().decode("utf-8")

    logger.info(
        "S3 다운로드 완료 | batch_date=%s | bytes=%s | etag=%s",
        args.batch_date,
        response["ContentLength"],
        response["ETag"]
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

    logger.info(
        "DB 트랜잭션 시작 | batch_date=%s",
        args.batch_date,
    )


    # DELETE + COPY
    with psycopg.connect(**db_params) as conn:
        # block < 하나의 명시적 트랜잭션
        with conn.transaction():
            with conn.cursor() as cur:
                # 01. 이번 실행 전용 temp table 생성 
                # stg_orders에 default 지정되어 있지 않은 batch_year와 같은 값들이 COPY로 복사해서 
                # 넣으려고 할 때, NOT NULL로 인해 오류 발생하지 않도록 따로 temp table 생성
                cur.execute(create_temp_sql)

                # 02. S3 CSV 원본 임시 테이블에 적재
                with cur.copy(copy_sql) as copy:
                    copy.write(csv_text)

                logger.info(
                    "임시 테이블 COPY 완료 | batch_date=%s | target=tmp_orders_raw",
                    args.batch_date,
                )

                # 03. 같은 배치 파티션의 이전 적재분 제거 - 멱등성 보장
                cur.execute(delete_sql, (year, month))
                deleted_rows = cur.rowcount

                logger.info(
                    "기존 staging 데이터 삭제 완료 | batch_date=%s | deleted_rows=%s",
                    args.batch_date,
                    deleted_rows,
                )
                

                # 04. metadata 붙여 영속 staging table에 적재
                cur.execute(insert_sql, (year, month, s3_key))
                inserted_rows = cur.rowcount

                logger.info(
                    "staging INSERT 완료 | batch_date=%s | inserted_rows=%s",
                    args.batch_date,
                    inserted_rows,
                )

    logger.info(
        "DB 트랜잭션 완료 | batch_date=%s | inserted_rows=%s",
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