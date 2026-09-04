CREATE TABLE IF NOT EXISTS etl_batch_runs (
    run_id UUID PRIMARY KEY,

    pipeline_name TEXT NOT NULL,

    batch_year SMALLINT NOT NULL,
    batch_month SMALLINT NOT NULL,

    source_s3_key TEXT,
    source_etag TEXT,

    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,

    status TEXT NOT NULL,
    temp_rows BIGINT,
    deleted_rows BIGINT,
    inserted_rows BIGINT,

    clean_rows BIGINT,
    quarantine_rows BIGINT,

    error_message TEXT,

    CONSTRAINT etl_batch_runs_month_check
        CHECK (batch_month BETWEEN 1 AND 12),

    CONSTRAINT etl_batch_runs_status_check
        CHECK (status IN ('running', 'success', 'failed'))
);