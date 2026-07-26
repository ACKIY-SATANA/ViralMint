# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
from sqlalchemy import Column, String, Text, DateTime, Float
from datetime import datetime
from uuid import uuid4
from backend.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), default="local", index=True)
    task_id = Column(String(100), nullable=True)  # asyncio task reference

    job_type = Column(String(30), nullable=False, index=True)
    # job_types: scout | download | analyze | generate | upload

    status = Column(String(20), default="pending", index=True)
    # statuses: pending | running | success | failed | cancelled

    title = Column(String(500), nullable=True)           # human-readable description
    progress_pct = Column(Float, default=0.0)            # 0-100
    current_step = Column(String(200), nullable=True)    # e.g. "Transcribing video 3/8"
    error_message = Column(Text, nullable=True)

    # Input/output tracking
    input_json = Column(Text, nullable=True)    # Job params (niche, platforms, etc.)
    output_json = Column(Text, nullable=True)   # Result references (video paths, etc.)

    # Cost tracking (for monetization foundation)
    estimated_cost_usd = Column(Float, default=0.0)

    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    # Heartbeat for the boot-time zombie sweep. EVERY accepted
    # update_job_status call touches this, and progress ticks route through it
    # via ws_manager.send_progress — so a recent updated_at means the job is
    # alive in SOME process, possibly a PREDECESSOR backend still draining an
    # in-flight job through a restart handoff. The sweep therefore fails only
    # STALE rows; a NULL heartbeat (rows written before this column existed —
    # the migration adds it without backfill) counts as stale, preserving the
    # old sweep-everything behaviour for old rows.
    updated_at = Column(DateTime, nullable=True, default=datetime.utcnow,
                        onupdate=datetime.utcnow)
