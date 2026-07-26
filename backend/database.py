# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text
from backend.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False},  # SQLite only
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable WAL mode for concurrent reads + faster writes."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")   # Wait up to 5s on lock contention
    cursor.execute("PRAGMA cache_size=-64000")   # 64MB cache
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Create all tables. Called once at startup from run.py."""
    # Import all models so Base knows about them
    from backend.models import (  # noqa: F401
        user_settings, user_behavior, feature_flag,
        job, scout_result, downloaded_video, generated_video,
        messaging_config, chat_session, user_profile,
        video_metrics, viral_formula,
        connected_channel, dynamic_template, caption_style,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Idempotent column additions for SQLite (no Alembic)
        await _add_column_if_missing(conn, "downloaded_videos", "transcript_segments_json", "TEXT")
        # Job heartbeat (zombie-sweep staleness signal — see models/job.py)
        await _add_column_if_missing(conn, "jobs", "updated_at", "DATETIME")
        await _add_column_if_missing(conn, "generated_videos", "source_type", "VARCHAR(30)")
        # Clip extraction fields
        await _add_column_if_missing(conn, "generated_videos", "clip_start_seconds", "FLOAT")
        await _add_column_if_missing(conn, "generated_videos", "clip_end_seconds", "FLOAT")
        await _add_column_if_missing(conn, "generated_videos", "clip_virality_score", "FLOAT")
        await _add_column_if_missing(conn, "generated_videos", "clip_hook_score", "FLOAT")
        await _add_column_if_missing(conn, "generated_videos", "clip_hook_type", "VARCHAR(30)")
        await _add_column_if_missing(conn, "generated_videos", "clip_virality_reason", "TEXT")
        await _add_column_if_missing(conn, "generated_videos", "clip_score_breakdown_json", "TEXT")
        await _add_column_if_missing(conn, "generated_videos", "caption_status", "VARCHAR(20)")
        await _add_column_if_missing(conn, "generated_videos", "metadata_status", "VARCHAR(20)")
        # BYOK: per-user encrypted keys (override .env at runtime)
        await _add_column_if_missing(conn, "user_settings", "ai_provider", "VARCHAR(20)")
        await _add_column_if_missing(conn, "user_settings", "ai_model", "VARCHAR(100)")
        await _add_column_if_missing(conn, "user_settings", "ai_api_key_encrypted", "TEXT")
        await _add_column_if_missing(conn, "user_settings", "youtube_api_key_encrypted", "TEXT")

    # Drift sentinel — compare every model's columns against the live DB
    # schema and log a loud warning for any column the model declares but
    # the DB lacks. This converts a future "endpoint 500" silent regression
    # (a model column added without a matching `_add_column_if_missing` line)
    # into a clear startup log line. Runs once, after migrations, before
    # zombie cleanup. Pure observability — never raises.
    await _warn_on_schema_drift()

    # Clean up zombie jobs — any jobs stuck at "running"/"pending" from a previous crash
    await _cleanup_zombie_jobs()


async def _warn_on_schema_drift():
    """Compare model schema vs live DB; log WARN for missing columns.

    Catches the class of regression where a column is added to a model
    but the matching `_add_column_if_missing` call is forgotten in
    init_db. Upgrade-install users hit a 500 on the first endpoint that
    SELECTs the new column; fresh installs work because the table is
    created from scratch. This sentinel makes the drift visible at
    startup so the next regression is caught before users file tickets.
    """
    try:
        async with engine.connect() as conn:
            for table_name, mapper in Base.metadata.tables.items():
                try:
                    rows = await conn.exec_driver_sql(f"PRAGMA table_info({table_name})")
                    db_cols = {r[1] for r in rows.fetchall()}
                except Exception:
                    # Table doesn't exist yet — create_all just made it, no drift possible.
                    continue
                if not db_cols:
                    continue
                model_cols = {c.name for c in mapper.columns}
                missing = model_cols - db_cols
                if missing:
                    logger.warning(
                        "Schema drift detected on table %r: model declares columns "
                        "missing from DB: %s. Add `_add_column_if_missing(conn, %r, ...)` "
                        "lines for each in backend/database.py init_db().",
                        table_name, sorted(missing), table_name,
                    )
    except Exception as e:
        logger.warning("Schema-drift sentinel failed (non-fatal): %s", e)


# A running/pending job whose heartbeat is younger than this is treated as
# ALIVE — possibly owned by a PREDECESSOR backend draining through a restart
# handoff. uvicorn frees its listening socket at the START of graceful
# shutdown and then keeps finishing background work, so a replacement instance
# can boot while the old one is still running a job: "port free" never meant
# "process gone". Progress ticks refresh the heartbeat every few seconds (see
# ws_manager.send_progress), so 180s tolerates the occasional long silent step
# (Whisper on a big file) without leaving genuinely dead jobs stuck "running"
# for more than a couple of minutes.
ZOMBIE_GRACE_SECONDS = 180


async def sweep_stale_jobs(grace_seconds: int = ZOMBIE_GRACE_SECONDS,
                           only_ids: list[str] | None = None) -> tuple[int, list[str]]:
    """Fail running/pending jobs whose heartbeat is STALE; leave fresh ones.

    Returns ``(swept_count, fresh_nonterminal_ids)``. A NULL heartbeat (rows
    written before the ``updated_at`` column existed) counts as stale — the
    same outcome those rows always got.

    ``only_ids`` scopes a pass to a known watch-set (the lifespan handoff
    watcher in backend/main.py) so jobs created by the CURRENT instance are
    never sweep candidates.

    Mis-sweeps self-heal: update_job_status permits terminal→terminal writes,
    so a draining predecessor's final "success" still lands over a "failed"
    this sweep wrote. Never raises.
    """
    from datetime import datetime, timedelta

    swept, fresh = 0, []
    try:
        async with AsyncSessionLocal() as db:
            from backend.models.job import Job
            from sqlalchemy import select
            q = select(Job).where(Job.status.in_(["running", "pending"]))
            if only_ids is not None:
                if not only_ids:
                    return 0, []
                q = q.where(Job.id.in_(only_ids))
            rows = (await db.execute(q)).scalars().all()
            cutoff = datetime.utcnow() - timedelta(seconds=grace_seconds)
            for job in rows:
                if job.updated_at is None or job.updated_at < cutoff:
                    job.status = "failed"
                    job.error_message = "Server restarted — job did not complete"
                    job.completed_at = datetime.utcnow()
                    swept += 1
                else:
                    fresh.append(job.id)
            if swept:
                await db.commit()
    except Exception as e:
        logger.warning(f"Zombie job sweep failed: {e}")
    return swept, fresh


async def _cleanup_zombie_jobs():
    """Boot-time sweep: fail STALE running/pending jobs from a previous session.

    Fresh ones are deliberately left alone for the lifespan handoff watcher
    (backend/main.py) — they may belong to a predecessor instance that is still
    draining, and the watcher sweeps them the moment they go stale.
    """
    swept, fresh = await sweep_stale_jobs()
    if swept:
        logger.warning(f"Marked {swept} zombie jobs as failed from previous session")
    if fresh:
        logger.info(
            "Boot sweep: %d running/pending job(s) still have a fresh heartbeat "
            "— possibly a draining predecessor; the handoff watcher will decide.",
            len(fresh),
        )


async def _add_column_if_missing(conn, table: str, column: str, col_type: str):
    """SQLite-safe column addition — no-op if already exists."""
    try:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
    except Exception:
        pass  # Column already exists — expected for idempotent migrations
