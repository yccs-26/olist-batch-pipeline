-- helper function
-- cast `::timestamp`로 진행하면 잘못된 행이 트랜잭션 실패를 야기할 수 있다.
CREATE OR REPLACE FUNCTION try_parse_timestamp(
    input_text TEXT
)
RETURNS TIMESTAMP
LANGUAGE plpgsql
IMMUTABLE
AS $$
BEGIN
    IF NULLIF(TRIM(input_text), '') IS NULL THEN
        RETURN NULL;
    END IF;

    RETURN TRIM(input_text)::TIMESTAMP;

EXCEPTION
    WHEN OTHERS THEN
        RETURN NULL;
END;
$$;