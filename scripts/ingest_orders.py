import boto3
from datetime import datetime
import pandas as pd

bucket = "olist-dataset-bychan"
s3 = boto3.client('s3')

# csv 파일 읽기
df = pd.read_csv("csvfiles/olist_orders_dataset.csv")

# 01. order_purchase_timestamp -> year & month 추출
opt = "order_purchase_timestamp"
df[opt] = pd.to_datetime(df[opt])

df["year"] = df[opt].dt.year
# df["month"] =df[opt].dt.month
# S3 파티션 경로 설정할 때, month 두 자리 문자열로 포맷팅되는 게 경로 정렬 및 조회에 용이하므로 format 신경 쓸 것!
df["month"] = df[opt].dt.strftime("%m")


original_cols = df.columns.to_list()

# 02. year, month - groupBy
# pandas의 groupBy는 그룹화 식별자와 dataframe이 반환됨.
for (year, month), group_df in df.groupby(['year', 'month']):
    print(f"기간 : {year}-{month}, 행 개수 : {len(group_df)}")

    key = f"raw/orders/year={year}/month={month}/orders_{year}_{month}.csv"


    # 03. 그룹화된 df를 bucket에 저장
    # encode : `put_object()`에 전달할 byte data로 변환
    csv_body = group_df[original_cols].to_csv(index=False).encode("utf-8")

    # `put_object()`의 `Body`는 객체 데이터로 bytes 또는 file-like object를 받는다
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=csv_body,
        ContentType="text/csv"
    )

print(f"업로드 완료: s3://{bucket}/{key}")