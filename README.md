
# Olist Orders 월별 배치 파이프라인

## 프로젝트 개요

이 프로젝트는 로컬의 Olist 주문 데이터를 월별로 Amazon S3 raw 영역에 적재하고, 특정 월 파티션을 PostgreSQL staging 테이블로 수집한 뒤, 데이터 품질 검증을 거쳐 clean 및 quarantine 테이블로 분리하는 배치 파이프라인입니다.

파이프라인은 단순히 데이터를 한 번 적재하는 데 그치지 않고, 다음 운영 요구사항을 고려해 구현했습니다.

- 지정한 월을 기준으로 과거 데이터를 다시 처리할 수 있는 backfill 구조
- 같은 월을 여러 번 실행해도 최종 결과가 중복되지 않는 멱등성
- S3 원천 파일 경로와 적재 시각을 보존하는 데이터 lineage
- S3와 PostgreSQL의 일시적 연결 오류에 대한 지수 백오프 재시도
- 정상 데이터와 오류 데이터를 분리하는 data quality 및 quarantine 구조
- 배치 실행 단위의 성공·실패·처리 건수를 남기는 audit 구조

## 아키텍처

전체 흐름은 다음과 같습니다.

```text
로컬 Olist orders CSV
        ↓
Amazon S3 raw 월별 파티션
        ↓
ingest_to_staging.py
        ↓
PostgreSQL stg_orders
        ↓
clean_orders.py
        ↓
clean_orders / quarantine_orders
        ↓
etl_batch_runs
```

원본 주문 CSV에서 주문 시각을 기준으로 연도와 월을 파생하고, 월별 S3 파티션으로 저장합니다. 이후 수집기는 실행 인자로 전달받은 배치 월에 해당하는 S3 객체를 읽어 staging에 적재합니다. clean 단계에서는 staging 데이터를 검증해 정상 행은 clean 테이블로, 검증 실패 행은 quarantine 테이블로 분리합니다.

## 데이터 계층

| 계층 | 저장 위치 | 목적 |
|---|---|---|
| Raw | S3 `raw/orders/year=YYYY/month=MM/` | 월별 원천 데이터를 보관하고, 재처리와 감사의 기준점으로 사용 |
| Staging | `stg_orders` | 원천 데이터를 최대한 유실 없이 저장하고, 정제 전 검증 대상 데이터로 사용 |
| Clean | `clean_orders` | 데이터 품질을 통과한 데이터를 강한 타입으로 변환하여 분석 가능한 형태로 제공 |
| Quarantine | `quarantine_orders` | 품질 규칙을 통과하지 못한 데이터와 거절 사유를 보관 |
| Audit | `etl_batch_runs` | 파이프라인 실행 단위의 상태, 시간, 처리 건수, 오류 요약을 기록 |

### Raw 계층

Raw 계층은 원본에 가장 가까운 데이터를 보관하는 영역입니다. 주문 구매 시각에서 연도와 월을 파생해 다음과 같은 규칙으로 S3에 저장합니다.

```text
raw/orders/year=2017/month=02/orders_2017_02.csv
```

월별 partition을 사용하면 특정 기간만 선택해 처리할 수 있습니다. 예를 들어 2017년 2월 파일이 수정되었거나 해당 월만 재처리해야 한다면, 전체 데이터를 다시 읽지 않고 2017-02 파티션만 다시 실행할 수 있습니다.

현재 raw 객체 key는 고정된 월별 경로를 사용합니다. 동일 key에 다시 업로드하면 기존 객체가 덮어써질 수 있으므로, 향후 원천 파일 버전까지 엄격하게 관리하려면 S3 Versioning 또는 별도 버전 경로를 도입할 수 있습니다.

### Staging 계층

`stg_orders`는 raw 데이터를 PostgreSQL로 옮긴 영속적인 중간 계층입니다. 원천 timestamp는 처음부터 강한 타입으로 변환하지 않고 문자형으로 보관합니다. 이유는 원천 데이터에 빈 문자열이나 형식이 깨진 날짜가 있어도, staging 적재 전체가 실패하지 않게 하기 위해서입니다.

staging에서는 다음 metadata를 보관합니다.

| 컬럼 | 의미 |
|---|---|
| `order_year`, `order_month` | 주문 구매 시각을 기준으로 원천 단계에서 파생한 비즈니스 데이터 |
| `batch_year`, `batch_month` | 이번 실행이 처리한 배치 파티션 |
| `source_s3_key` | 해당 데이터가 온 정확한 S3 raw 객체 경로 |
| `loaded_at` | 현재 staging 행이 마지막으로 적재된 시각 |

`order_year/month`와 `batch_year/month`는 현재 대부분 같지만 의미가 다릅니다. 주문 시점과 실제 처리 파티션을 분리하면, 잘못된 파일이 올라간 경우, 늦게 도착한 데이터, backfill, 재처리, 원천 수정 상황을 추적할 수 있습니다.

### Clean 계층

`clean_orders`는 데이터 품질 검증을 통과한 분석용 데이터입니다. 문자열로 저장된 날짜 컬럼은 안전하게 `TIMESTAMP`로 변환하며, 빈 문자열은 `NULL`로 정규화합니다.

clean에서는 주문 구매 시각을 기준으로 `order_year`, `order_month`를 다시 계산하여 강한 타입으로 저장합니다. 따라서 clean 데이터의 날짜와 파생 연·월 값은 항상 일관성을 가져야 합니다.

### Quarantine 계층

`quarantine_orders`는 오류 행을 버리지 않고 보존하기 위한 테이블입니다. 오류가 있는 행은 원본 timestamp 문자열, batch 정보, S3 source key, staging 적재 시각, 거절 사유와 함께 저장됩니다.

이 구조를 사용하면 데이터가 clean에 들어가지 못했을 때 단순히 "데이터가 줄었다"가 아니라, 어떤 행이 왜 제외되었는지 확인할 수 있습니다.

## 월 단위 멱등 적재

staging과 clean 단계는 모두 월 단위의 delete-and-insert 방식으로 동작합니다.

```text
기존 batch 데이터 삭제
→ 같은 batch의 새로운 결과 삽입
→ transaction commit
```

예를 들어 2017년 2월을 두 번 실행하면 첫 실행에서는 기존 데이터가 없으므로 삭제 건수가 0이고, 두 번째 실행에서는 기존 2월 데이터가 삭제된 뒤 같은 수의 데이터가 다시 적재됩니다.

```text
첫 실행: 삭제 0건, 삽입 N건
두 번째 실행: 삭제 N건, 삽입 N건
최종 결과: N건
```

즉, 실행 횟수가 늘어나도 동일한 원천과 동일한 규칙이라면 최종 데이터 결과는 변하지 않습니다. 이것이 멱등성입니다.

멱등성은 다음 상황에서 중요합니다.

- 네트워크 오류 후 같은 월 재실행
- S3 파일 수정 후 재적재
- 과거 월 전체 backfill
- Airflow task 재시도
- 운영자가 특정 월만 수동 재처리

## Transaction과 Temporary Table

staging 적재 시에는 S3 CSV를 바로 `stg_orders`에 넣지 않고, 우선 `tmp_orders_raw` temporary table에 대량 적재합니다.

```text
S3 CSV
→ tmp_orders_raw에 COPY
→ 기존 staging batch DELETE
→ tmp_orders_raw에서 stg_orders INSERT
→ commit
```

clean 처리에서도 `tmp_classified_orders` temporary table을 사용합니다.

```text
stg_orders
→ normalized, parsed, classified 단계 수행
→ tmp_classified_orders 생성
→ valid 행은 clean_orders로 INSERT
→ invalid 행은 quarantine_orders로 INSERT
→ 검증 후 commit
```

temporary table은 transaction 내부에서만 사용하는 작업용 중간 테이블입니다. `ON COMMIT DROP`을 사용해 transaction이 끝나면 자동으로 제거되도록 했습니다.

이를 사용하는 이유는 다음과 같습니다.

- 긴 데이터 품질 분류 로직을 한 번만 계산
- clean과 quarantine이 동일한 분류 결과를 사용
- source, clean, quarantine 행 수를 같은 스냅샷 기준으로 검증
- 중간 오류가 발생하면 transaction rollback으로 이전 결과를 유지
- 배치 종료 후 중간 테이블이 남지 않음

## 데이터 품질 규칙

clean 단계에서는 각 staging 행을 정상 또는 오류로 분류합니다.

| 규칙 | 의미 | 처리 |
|---|---|---|
| `ORDER_ID_REQUIRED` | 주문 ID가 없거나 빈 문자열 | Quarantine |
| `ORDER_STATUS_REQUIRED` | 주문 상태가 없거나 빈 문자열 | Quarantine |
| `PURCHASE_TIMESTAMP_REQUIRED` | 주문 구매 시각이 없거나 빈 문자열 | Quarantine |
| `PURCHASE_TIMESTAMP_INVALID` | 주문 구매 시각은 존재하지만 timestamp 변환 실패 | Quarantine |
| `OPTIONAL_TIMESTAMP_INVALID` | 선택 timestamp 값은 존재하지만 변환 실패 | Quarantine |
| `ORDER_PARTITION_MISMATCH` | purchase timestamp의 연·월과 원천 `order_year/month`가 다름 | Quarantine |

승인 시각, 배송 시작 시각, 고객 배송 완료 시각, 예상 배송 시각은 모든 주문에서 필수는 아닙니다. 주문 취소, 배송 불가, 미완료 상태에서는 값이 없을 수 있으므로 빈 값 자체는 clean에서 `NULL`로 허용합니다. 다만 값이 존재하는데 형식이 올바르지 않으면 오류로 분류합니다.

## Row Count 검증

clean 파이프라인은 데이터 누락을 탐지하기 위해 다음 보존 법칙을 검증합니다.

```text
staging source rows
= clean inserted rows
+ quarantine inserted rows
```

분류 결과와 실제 INSERT 결과가 다르면 Python에서 예외를 발생시킵니다. 예외는 transaction 내부에서 발생하므로, 해당 월의 DELETE와 INSERT가 모두 rollback됩니다.

즉, 단순히 "INSERT가 성공했다"가 아니라, 해당 월 staging의 모든 행이 clean 또는 quarantine 중 정확히 한 곳으로 분류되었는지 확인합니다.

## Timestamp 정책

이 프로젝트는 비즈니스 이벤트 시간과 파이프라인 시스템 시간을 구분합니다.

| 구분 | 예시 | 타입 | 의미 |
|---|---|---|---|
| 비즈니스 시간 | 주문 구매, 승인, 배송 시간 | `TIMESTAMP` | 원천 시스템이 제공한 timezone 없는 날짜·시간 |
| 시스템 이벤트 시간 | 적재, 정제, 격리, 실행 시작·종료 시각 | `TIMESTAMPTZ` | 파이프라인이 실제로 수행된 시점 |

Olist 주문 timestamp는 timezone offset이 없는 문자열입니다. 이를 임의로 한국 시간이나 UTC로 해석하지 않기 위해 clean의 주문 관련 시간은 `TIMESTAMP`로 저장합니다.

반면 `loaded_at`, `cleaned_at`, `quarantined_at`, `started_at`, `finished_at`은 파이프라인 실행 시점이므로 timezone을 포함하는 `TIMESTAMPTZ`로 저장합니다. 이는 로컬, Docker, AWS, Airflow 등 실행 환경이 달라져도 동일한 실제 시점을 비교하기 위함입니다.

## Retry 전략

Tenacity를 사용해 일시적인 외부 시스템 장애에만 재시도를 적용했습니다.

### S3 다운로드 재시도

S3 다운로드 함수는 endpoint 연결 실패, connection timeout, read timeout, 연결 종료와 같은 일시적 네트워크 오류를 재시도합니다.

재시도하지 않는 오류는 다음과 같습니다.

- 존재하지 않는 S3 key
- 접근 권한 부족
- 잘못된 AWS credential
- CSV 형식 또는 컬럼 수 불일치

### PostgreSQL 적재 재시도

PostgreSQL은 연결 또는 통신 오류에 대해서만 재시도합니다. 재시도 범위는 단일 INSERT가 아니라 전체 transaction입니다.

```text
새 DB connection
→ temporary table 생성
→ COPY 또는 분류
→ 기존 batch DELETE
→ 새 데이터 INSERT
→ row count 검증
→ commit
```

중간 단계만 재시도하면 temporary table이나 transaction 상태를 신뢰할 수 없기 때문에, 새 connection에서 배치 작업 전체를 처음부터 다시 수행합니다.

## Audit 실행 이력

`etl_batch_runs`는 파이프라인 실행 1회당 1행을 저장하는 audit 테이블입니다.

| 컬럼 | 의미 |
|---|---|
| `run_id` | 실행 1회를 식별하는 UUID |
| `pipeline_name` | 파이프라인 단계 이름 |
| `batch_year`, `batch_month` | 처리 대상 월 |
| `source_s3_key` | ingest 단계에서 사용한 S3 객체 경로 |
| `source_etag` | S3 응답의 객체 식별 정보 |
| `started_at`, `finished_at` | 실행 시작·종료 시각 |
| `status` | `running`, `success`, `failed` 상태 |
| row count 지표 | source, temp, deleted, inserted, clean, quarantine 처리 건수 |
| `error_message` | 실패 시 간단한 오류 요약 |

### 성공 audit

성공 audit은 staging 또는 clean transaction이 정상 commit된 뒤에 기록합니다.

```text
데이터 transaction commit 성공
→ 별도 audit connection 생성
→ success audit INSERT
```

이 순서는 아직 commit되지 않은 작업을 성공으로 기록하는 문제를 줄입니다.

### 실패 audit

실패 audit은 main transaction과 별도의 DB connection에서 기록합니다.

```text
main data transaction 실패
→ rollback
→ 별도 audit connection으로 failed audit INSERT
→ 원래 예외를 다시 raise
```

실패 audit을 main transaction 안에 기록하면 rollback 시 실패 기록도 함께 사라질 수 있습니다. 따라서 실패 사실을 남기기 위해 별도 connection을 사용합니다.

상세 traceback은 애플리케이션 log에 남기고, audit 테이블에는 긴 stack trace 대신 간단한 예외 타입과 메시지만 저장합니다.

## Logging

각 배치 실행에서 다음 이벤트를 로그로 기록합니다.

- 배치 시작과 대상 `batch_date`
- S3 bucket 및 source key
- S3 다운로드 시작과 완료
- S3 객체 크기와 ETag
- DB transaction 시작과 완료
- temporary table 생성 완료
- COPY 완료와 temp row count
- 기존 batch DELETE 행 수
- staging, clean, quarantine INSERT 행 수
- 품질 분류 결과: source, valid, invalid 행 수
- clean/quarantine 적재 결과 검증
- audit 기록 완료
- 배치 성공과 전체 실행 시간
- 배치 실패와 traceback

민감한 정보는 로그에 기록하지 않습니다.

- PostgreSQL password
- AWS access key
- AWS secret key
- 전체 connection string

## Backfill

orders raw 데이터는 실제 S3 객체 목록을 기준으로 다음 25개 월 파티션을 대상으로 backfill했습니다.

```text
2016-09
2016-10
2016-12
2017-01 ~ 2017-12
2018-01 ~ 2018-10
```

2016-11은 raw S3 객체가 존재하지 않으므로 backfill 대상에서 제외했습니다.

전체 backfill은 첫 실행 시 월별 순차 실행으로 처리했습니다. 순차 실행은 중간 실패 시 실패한 월을 정확히 확인하고, 수정 뒤 해당 월부터 재개하기 쉽습니다.

## 검증 결과 기준

전체 backfill 이후에는 아래 항목을 검증합니다.

- staging의 배치 파티션 수와 S3 raw 월 목록이 일치하는가
- 각 월의 staging row count가 ingest 결과와 일치하는가
- 각 월에서 `staging rows = clean rows + quarantine rows`가 성립하는가
- clean timestamp에서 계산한 year/month가 clean의 `order_year/month`와 일치하는가
- `order_id`가 NULL 또는 중복되지 않는가
- quarantine에 들어간 데이터가 있다면 reason별 건수를 확인할 수 있는가
- 같은 월을 재실행해도 최종 row count가 증가하지 않는가
- audit 테이블에서 실행 상태, 시간, 처리 건수, 오류 요약을 확인할 수 있는가

## 보안 및 저장소 관리

실제 비밀번호와 AWS 자격증명은 코드에 하드코딩하지 않습니다.

- PostgreSQL 설정은 로컬 `.env`에서 관리
- boto3는 `aws configure` 또는 `~/.aws/credentials` 기반 AWS credential provider chain 사용
- `.env` 파일은 Git에 올리지 않음
- 실행 로그, 가상환경, Python cache도 Git에서 제외

권장 Git 제외 대상은 다음과 같습니다.

```text
.env
.venv/
logs/
__pycache__/
Python bytecode 파일
```

## 프로젝트에서 학습한 점

이 프로젝트를 통해 다음 데이터 엔지니어링 개념을 구현했습니다.

- S3 raw partitioning
- batch date 기반 deterministic processing
- PostgreSQL COPY FROM STDIN 기반 대량 적재
- Docker PostgreSQL 환경에서의 client-side COPY 처리
- temporary table과 transaction을 이용한 원자적 적재
- delete-and-insert 기반 월 단위 멱등성
- 데이터 lineage를 위한 source S3 key와 적재 metadata
- raw, staging, clean, quarantine 계층 분리
- data quality classification과 오류 행 격리
- row count reconciliation
- 지수 백오프 retry
- batch run audit과 성공·실패 이력 관리
- 전체 기간 backfill 및 재처리 가능 구조

## 향후 확장 방향

- `order_items`와 `payments` 데이터에 동일한 raw → staging → clean/quarantine 패턴 적용
- customers, products, sellers 데이터를 dimension 형태로 정제
- 주문 상품 단위의 `fact_order_items` analytics mart 구축
- `dim_date`, `dim_customer`, `dim_product`, `dim_seller` 설계
- Airflow로 ingest → clean → mart task dependency 오케스트레이션
- Airflow logical date 기반 월별 scheduling과 명시적 backfill
- pytest 기반 단위 테스트와 데이터 품질 테스트 추가
- GitHub Actions를 통한 lint, test 자동화
- S3 Versioning 및 source version metadata 관리