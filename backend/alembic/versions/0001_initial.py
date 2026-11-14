"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-14
"""

from alembic import op

from app.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


AGGREGATES = {
    "metric_samples_1m": "1 minute",
    "metric_samples_5m": "5 minutes",
    "metric_samples_1h": "1 hour",
    "metric_samples_1d": "1 day",
}


def _timescale_available() -> bool:
    row = op.get_bind().exec_driver_sql(
        "SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb')"
    ).scalar()
    return bool(row)


def upgrade() -> None:
    has_timescale = _timescale_available()
    with op.get_context().autocommit_block():
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        if has_timescale:
            op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    Base.metadata.create_all(bind=op.get_bind())
    if not has_timescale:
        return
    with op.get_context().autocommit_block():
        op.execute(
            "SELECT create_hypertable('metric_samples', 'timestamp', if_not_exists => TRUE, migrate_data => TRUE)"
        )
        for name, bucket in AGGREGATES.items():
            op.execute(
                f"""
                CREATE MATERIALIZED VIEW IF NOT EXISTS {name}
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket(INTERVAL '{bucket}', timestamp) AS bucket,
                    tenant_id,
                    host_id,
                    service_id,
                    metric_name,
                    source_type,
                    avg(value) AS avg_value,
                    min(value) AS min_value,
                    max(value) AS max_value,
                    count(*) AS sample_count
                FROM metric_samples
                GROUP BY 1, 2, 3, 4, 5, 6
                WITH NO DATA
                """
            )
        op.execute(
            """
            SELECT add_continuous_aggregate_policy('metric_samples_1m',
                start_offset => INTERVAL '2 hours',
                end_offset => INTERVAL '1 minute',
                schedule_interval => INTERVAL '1 minute',
                if_not_exists => TRUE)
            """
        )
        op.execute(
            """
            SELECT add_continuous_aggregate_policy('metric_samples_5m',
                start_offset => INTERVAL '1 day',
                end_offset => INTERVAL '5 minutes',
                schedule_interval => INTERVAL '5 minutes',
                if_not_exists => TRUE)
            """
        )
        op.execute(
            """
            SELECT add_continuous_aggregate_policy('metric_samples_1h',
                start_offset => INTERVAL '7 days',
                end_offset => INTERVAL '1 hour',
                schedule_interval => INTERVAL '1 hour',
                if_not_exists => TRUE)
            """
        )
        op.execute(
            """
            SELECT add_continuous_aggregate_policy('metric_samples_1d',
                start_offset => INTERVAL '90 days',
                end_offset => INTERVAL '1 day',
                schedule_interval => INTERVAL '1 day',
                if_not_exists => TRUE)
            """
        )
        op.execute(
            "SELECT add_retention_policy('metric_samples', INTERVAL '90 days', if_not_exists => TRUE)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(list(AGGREGATES)):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
    Base.metadata.drop_all(bind=bind)
