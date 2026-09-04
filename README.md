S3 raw
  └─ 월별 원천 CSV, 재처리 가능한 원본

stg_orders
  └─ 원천을 TEXT 중심으로 보존
  └─ source_s3_key, batch_year/month, loaded_at 보관

clean_orders
  └─ 검증 통과 행만 저장
  └─ timestamp를 TIMESTAMPTZ로 변환
  └─ 분석·조인에 사용할 정상화 테이블

quarantine_orders
  └─ 검증 실패 행을 원본값과 사유와 함께 보관
  └─ 운영자가 재검토하거나 규칙을 개선할 근거

--------------------------------------------------------------------------------------------------------------------------------------

stg_orders의 특정 batch 월 선택
→ timestamp를 안전하게 파싱
→ 품질 규칙별 rejection_reason 계산
→ valid 행 수 / invalid 행 수 확인
→ invalid 이유별 건수 확인

valid / invalid를 별도 SQL로 계산할 경우, 한쪽만 수정되었을 때 valid/invalid 합계가 전체 staging 행 수와 맞지 않을 수 있다.
→  공통 CTE에서 한 번만 분류하고, CTE를 바탕으로 집계한다.

stg_orders
→ normalized CTE
→ classified CTE
→ valid / invalid 집계

| 우선순위 | 조건                                                                  | rejection_reason            |
| ---- | ------------------------------------------------------------------- | --------------------------- |
| 1    | order_id가 NULL 또는 빈 문자열                                             | ORDER_ID_REQUIRED           |
| 2    | order_status가 NULL 또는 빈 문자열                                         | ORDER_STATUS_REQUIRED       |
| 3    | order_purchase_timestamp가 NULL 또는 빈 문자열                             | PURCHASE_TIMESTAMP_REQUIRED |
| 4    | purchase timestamp에 값은 있지만 parsing 실패                               | PURCHASE_TIMESTAMP_INVALID  |
| 5    | optional timestamp에 값은 있지만 parsing 실패                               | OPTIONAL_TIMESTAMP_INVALID  |
| 6    | parse한 purchase timestamp의 year/month와 staging order_year/month 불일치 | ORDER_PARTITION_MISMATCH    |
| 7    | 그 외                                                                 | NULL → clean 대상             |


--------------------------------------------------------------------------------------------------------------------------------------

stg_orders의 batch=2017-02
→ 품질 규칙으로 valid / invalid 분류
→ 기존 clean 2017-02 삭제
→ 기존 quarantine 2017-02 삭제
→ valid → clean_orders INSERT
→ invalid → quarantine_orders INSERT
→ 건수 검증
→ commit

GOAL : 한 달을 수동 실행해 transaction·멱등성·row count를 검증
- clean / quarantine 따로 commit 하지 않고, 하나의 transaction으로 묶기

--------------------------------------------------------------------------------------------------------------------------------------

clean_orders.py

--batch-date 받기
→ DB 설정 로드 및 검증
→ transaction 시작
→ 해당 batch clean/quarantine 기존 데이터 DELETE
→ tmp_classified_orders 생성
→ clean INSERT
→ quarantine INSERT
→ source/clean/quarantine row count 확인
→ 불일치면 RuntimeError로 rollback
→ commit
→ logging으로 결과 기록


--------------------------------------------------------------------------------------------------------------------------------------

# audit TABLE - 실행 이력 테이블


**batch run 이력 알 수 없다.**
stg_orders.loaded_at
→ 현재 staging row가 마지막으로 적재된 시각

clean_orders.cleaned_at
→ 현재 clean row가 마지막으로 clean된 시각

**현재 알 수 없는 정보**
2017-02를 과거에 몇 번 실행했는가?
어느 실행이 실패했는가?
처리 건수는 매번 몇 건이었는가?
첫 실행과 마지막 실행의 소요 시간은?
어느 S3 object / ETag를 읽었는가?
→ batch run 단위 audit 테이블

> 배치 실행 전체에 대한 정보 ⇒ 운영 metadata
S3 파일 읽는 단계에서 실패 시, stg_orders에 주문 행이 한 건도 들어가지 않는다.
이때, 이 시도에 대한 정보를 담을 수 있는 테이블이 필요하다.