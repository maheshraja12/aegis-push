"""
==============================================================================
Aegis V3: Enterprise Autonomous Infrastructure Resilience Engine
aegis_v3/persistence.py — Async SQLite Audit Log & Raft Persistence Backend
==============================================================================

PURPOSE
-------
This module provides two independent async backends:

  1. AuditLogBackend
     ─────────────────
     Persists every PipelineResult to an SQLite database so the full incident
     history survives process restarts. Exposes:
       - save_pipeline_run(result)  → inserts or replaces a PipelineRunRecord
       - get_recent_runs(limit)     → returns the N most recent runs
       - get_run(run_id)            → fetch a specific run by ID
       - save_mttr(record)          → insert a MttrRecord
       - get_mttr_stats()           → aggregate MTTR stats (min/max/avg)

  2. RaftPersistenceBackend
     ───────────────────────
     Gives the Raft consensus module crash-safe durable storage for:
       - currentTerm               — Must survive restarts (Raft §5.4)
       - votedFor                  — Prevents double-voting after restart
       - log entries               — The committed command history

     Each RaftNode calls this backend whenever its state transitions.
     On startup, nodes call `load_state()` to restore their last term/vote.

==============================================================================
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import aiosqlite

from aegis_v3.schema_v3 import MttrRecord, PipelineResult, PipelineRunRecord, StageResult

logger = logging.getLogger("aegis.persistence")

# ---------------------------------------------------------------------------
# Database path resolution
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "aegis_v3_audit.db",
)


# ---------------------------------------------------------------------------
# AuditLogBackend
# ---------------------------------------------------------------------------

class AuditLogBackend:
    """
    Async SQLite backend for the Aegis V3 pipeline audit trail.

    Usage:
        backend = AuditLogBackend()
        await backend.initialize()
        await backend.save_pipeline_run(result)
        runs = await backend.get_recent_runs(20)
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._initialized = False
        logger.info(f"AuditLogBackend: db_path={db_path}")

    async def initialize(self) -> None:
        """Create tables if they don't exist. Idempotent."""
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id          TEXT PRIMARY KEY,
                    incident_id     TEXT,
                    severity        TEXT,
                    success         INTEGER,
                    total_duration_us REAL,
                    stage_count     INTEGER,
                    deployed_branch TEXT,
                    summary         TEXT,
                    started_at      TEXT,
                    completed_at    TEXT,
                    stages_json     TEXT,
                    mttr_seconds    REAL,
                    chaos_bug_type  TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS mttr_records (
                    record_id       TEXT PRIMARY KEY,
                    chaos_id        TEXT,
                    bug_type        TEXT,
                    target_file     TEXT,
                    injected_at     TEXT,
                    resolved_at     TEXT,
                    mttr_seconds    REAL,
                    pipeline_run_id TEXT,
                    resolved        INTEGER
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_runs_started ON pipeline_runs (started_at DESC)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_mttr_chaos_id ON mttr_records (chaos_id)
            """)
            await db.commit()
        self._initialized = True
        logger.info("AuditLogBackend: tables initialized.")

    async def save_pipeline_run(self, result: PipelineResult) -> PipelineRunRecord:
        """Persist a PipelineResult as a PipelineRunRecord row."""
        stages_json = json.dumps(
            [s.model_dump() for s in result.stages],
            default=str,
        )
        record = PipelineRunRecord(
            run_id=result.run_id,
            incident_id=result.incident_id,
            severity=result.severity.value,
            success=result.success,
            total_duration_us=result.total_duration_us,
            stage_count=len(result.stages),
            deployed_branch=result.deployed_branch,
            summary=result.summary,
            started_at=result.started_at,
            completed_at=result.completed_at,
            stages_json=stages_json,
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO pipeline_runs
                (run_id, incident_id, severity, success, total_duration_us,
                 stage_count, deployed_branch, summary, started_at, completed_at,
                 stages_json, mttr_seconds, chaos_bug_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.run_id, record.incident_id, record.severity,
                int(record.success), record.total_duration_us, record.stage_count,
                record.deployed_branch, record.summary, record.started_at,
                record.completed_at, record.stages_json, record.mttr_seconds,
                record.chaos_bug_type,
            ))
            await db.commit()
        logger.info(f"AuditLogBackend: saved run {result.run_id} | success={result.success}")
        return record

    async def save_mttr(self, mttr: MttrRecord) -> None:
        """Persist an MttrRecord."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO mttr_records
                (record_id, chaos_id, bug_type, target_file, injected_at,
                 resolved_at, mttr_seconds, pipeline_run_id, resolved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                mttr.record_id, mttr.chaos_id, mttr.bug_type, mttr.target_file,
                mttr.injected_at, mttr.resolved_at, mttr.mttr_seconds,
                mttr.pipeline_run_id, int(mttr.resolved),
            ))
            await db.commit()
        logger.info(
            f"AuditLogBackend: saved MTTR record {mttr.record_id} | "
            f"mttr={mttr.mttr_seconds}s"
        )

    async def get_recent_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the N most recent pipeline run records as dicts."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT run_id, incident_id, severity, success, total_duration_us,
                       stage_count, deployed_branch, summary, started_at, completed_at,
                       mttr_seconds, chaos_bug_type
                FROM pipeline_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        """Fetch a single run by run_id including full stages_json."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["stages"] = json.loads(d.get("stages_json", "[]"))
        except json.JSONDecodeError:
            d["stages"] = []
        return d

    async def get_mttr_stats(self) -> dict[str, Any]:
        """Compute aggregate MTTR statistics across all resolved incidents."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                """
                SELECT COUNT(*) as total, COUNT(mttr_seconds) as resolved,
                       MIN(mttr_seconds) as min_s, MAX(mttr_seconds) as max_s,
                       AVG(mttr_seconds) as avg_s
                FROM mttr_records WHERE resolved = 1
                """
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return {"total": 0, "resolved": 0}
        return {
            "total_chaos_incidents": row[0],
            "resolved_incidents": row[1],
            "min_mttr_seconds": row[2],
            "max_mttr_seconds": row[3],
            "avg_mttr_seconds": row[4],
        }


# ---------------------------------------------------------------------------
# RaftPersistenceBackend
# ---------------------------------------------------------------------------

class RaftPersistenceBackend:
    """
    Crash-safe durable storage for a single Raft node's volatile state.

    Per Raft §5.4: Before responding to ANY RPC, a server must persist
    `currentTerm` and `votedFor` to stable storage.

    Each node gets its own row keyed by `node_id`. This backend is
    synchronous-safe for use inside asyncio (all writes are tiny and fast).
    """

    _TABLE_DDL = """
        CREATE TABLE IF NOT EXISTS raft_node_state (
            node_id         TEXT PRIMARY KEY,
            current_term    INTEGER DEFAULT 0,
            voted_for       TEXT,
            log_json        TEXT DEFAULT '[]',
            updated_at      TEXT
        )
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH, node_id: str = "") -> None:
        self._db_path = db_path
        self._node_id = node_id

    async def ensure_table(self) -> None:
        """Create the raft_node_state table if not exists."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(self._TABLE_DDL)
            await db.commit()

    async def persist_state(
        self,
        current_term: int,
        voted_for: Optional[str],
        log_entries: list[dict],
    ) -> None:
        """Atomically persist node state. Called before every RPC response."""
        import datetime
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(self._TABLE_DDL)
            await db.execute(
                """
                INSERT OR REPLACE INTO raft_node_state
                (node_id, current_term, voted_for, log_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    self._node_id,
                    current_term,
                    voted_for,
                    json.dumps(log_entries, default=str),
                    datetime.datetime.utcnow().isoformat() + "Z",
                ),
            )
            await db.commit()

    async def load_state(self) -> tuple[int, Optional[str], list[dict]]:
        """
        Restore node state from disk on startup.

        Returns:
            (current_term, voted_for, log_entries)
        """
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(self._TABLE_DDL)
            await db.commit()
            async with db.execute(
                "SELECT current_term, voted_for, log_json FROM raft_node_state WHERE node_id = ?",
                (self._node_id,),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return 0, None, []

        current_term = row[0] or 0
        voted_for    = row[1]
        try:
            log_entries = json.loads(row[2] or "[]")
        except json.JSONDecodeError:
            log_entries = []

        logger.info(
            f"RaftPersistenceBackend [{self._node_id}]: "
            f"restored term={current_term}, voted_for={voted_for}, "
            f"log_entries={len(log_entries)}"
        )
        return current_term, voted_for, log_entries
