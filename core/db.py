"""SQLite/WAL database layer with migrations (core/db).

Assumption A-004: SQLite with WAL is the v1 store (spec §3.10 / D-006).
Connections are thread-local; migrations are forward-only and run in a transaction.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

Migration = Callable[[sqlite3.Connection], None]
_MIGRATIONS: List[tuple[str, Migration]] = []


def migration(version: str):
    def deco(fn: Migration) -> Migration:
        _MIGRATIONS.append((version, fn))
        return fn
    return deco


class Database:
    def __init__(self, path: str | Path, wal: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.wal = wal
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init()

    # ------------------------------------------------------------- connection
    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            if self.wal:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _init(self) -> None:
        with self._lock:
            c = self.conn
            c.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                      "version TEXT PRIMARY KEY, applied_at INTEGER)")
            self.migrate()

    def migrate(self) -> List[str]:
        applied = {r["version"] for r in self.conn.execute("SELECT version FROM schema_migrations")}
        done: List[str] = []
        for version, fn in sorted(_MIGRATIONS, key=lambda x: x[0]):
            if version in applied:
                continue
            with self._lock:
                self.conn.execute("BEGIN")
                try:
                    fn(self.conn)
                    self.conn.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES(?, strftime('%s','now'))",
                        (version,))
                    self.conn.execute("COMMIT")
                    done.append(version)
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise
        return done

    # ---------------------------------------------------------------- helpers
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
        return list(self.conn.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def in_transaction(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._lock:
            self.conn.execute("BEGIN")
            try:
                result = fn(self.conn)
                self.conn.execute("COMMIT")
                return result
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None


# ---------------------------------------------------------------------------
# Core schema (v1)
# ---------------------------------------------------------------------------
@migration("001_core")
def _m001(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE memory_records (
        record_id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        entity TEXT,
        value TEXT NOT NULL,
        confidence REAL DEFAULT 0.7,
        source TEXT,
        person_id TEXT,
        project TEXT,
        mission_id TEXT,
        privacy_scope TEXT DEFAULT 'private:owner',
        data_class TEXT DEFAULT 'INTERNAL',
        created_at INTEGER,
        updated_at INTEGER,
        version INTEGER DEFAULT 1,
        superseded_by TEXT,
        pinned INTEGER DEFAULT 0,
        metadata TEXT DEFAULT '{}'
    )""")
    conn.execute("CREATE INDEX idx_mem_person ON memory_records(person_id)")
    conn.execute("CREATE INDEX idx_mem_type ON memory_records(type)")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts"
                 " USING fts5(record_id UNINDEXED, entity, value, tokenize='unicode61')")
    conn.execute("""CREATE TABLE missions (
        mission_id TEXT PRIMARY KEY,
        goal TEXT,
        owner TEXT,
        state TEXT,
        targets TEXT DEFAULT '[]',
        criteria TEXT DEFAULT '[]',
        trace_id TEXT,
        created_at INTEGER,
        updated_at INTEGER,
        cost_usd REAL DEFAULT 0,
        errors TEXT DEFAULT '[]'
    )""")
    conn.execute("""CREATE TABLE mission_steps (
        step_id TEXT PRIMARY KEY,
        mission_id TEXT,
        type TEXT,
        capability TEXT,
        params TEXT DEFAULT '{}',
        status TEXT,
        result TEXT DEFAULT '{}',
        seq INTEGER
    )""")
    conn.execute("""CREATE TABLE audit_log (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER, who TEXT, device TEXT, action TEXT,
        why TEXT, mission_id TEXT, result TEXT, trace_id TEXT,
        prev_hash TEXT, hash TEXT
    )""")
    conn.execute("""CREATE TABLE grants (
        grant_id TEXT PRIMARY KEY,
        principal TEXT, scope TEXT, grant_type TEXT,
        granted_by TEXT, granted_at INTEGER, expires_at INTEGER,
        revoked_at INTEGER, reason TEXT, consumed INTEGER DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE provider_state (
        provider_id TEXT, model_id TEXT,
        state TEXT DEFAULT 'healthy',
        error_count INTEGER DEFAULT 0,
        last_error TEXT, last_ok INTEGER, last_fail INTEGER,
        total_calls INTEGER DEFAULT 0, total_cost REAL DEFAULT 0,
        PRIMARY KEY(provider_id, model_id)
    )""")
    # Runtime provider eligibility (Point 1 — automatic health state). This is
    # SEPARATE from the owner's saved configuration: a transient failure marks a
    # provider temporarily ineligible here without ever touching enabled/config.
    conn.execute("""CREATE TABLE provider_runtime (
        provider_id TEXT PRIMARY KEY,
        status TEXT DEFAULT 'ready',
        detail TEXT DEFAULT '',
        since_ms INTEGER DEFAULT 0,
        until_ms INTEGER DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE artifacts (
        artifact_id TEXT PRIMARY KEY,
        mission_id TEXT, path TEXT, version INTEGER DEFAULT 1,
        hash TEXT, creator TEXT, metadata TEXT DEFAULT '{}', created_at INTEGER
    )""")
    conn.execute("""CREATE TABLE locks (
        resource TEXT PRIMARY KEY,
        holder TEXT, lease_until INTEGER, acquired_at INTEGER
    )""")


@migration("002_phase2_computer")
def _m002(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS app_launch_cache (
        name TEXT NOT NULL,
        method TEXT NOT NULL,
        target TEXT NOT NULL,
        ok_count INTEGER DEFAULT 0,
        fail_count INTEGER DEFAULT 0,
        last_ok INTEGER DEFAULT 0,
        PRIMARY KEY (name, method, target)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS action_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mission_id TEXT, capability TEXT, strategy TEXT,
        ok INTEGER, detail TEXT, attempts INTEGER,
        latency_ms INTEGER, ts INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS workspace_index (
        path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, hash TEXT, indexed_at INTEGER
    )""")


@migration("003_voice")
def _m003(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS voice_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        turn_id TEXT, ts INTEGER,
        vad_ms INTEGER, stt_ms INTEGER, director_ms INTEGER, action_ms INTEGER,
        tts_first_audio_ms INTEGER, tts_ms INTEGER, total_ms INTEGER,
        barge_in_stop_ms INTEGER, interrupted INTEGER DEFAULT 0,
        stt_provider TEXT, tts_provider TEXT, transcript TEXT, reply TEXT
    )""")


@migration("004_plugins")
def _m004(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS plugins (
        id TEXT PRIMARY KEY,
        path TEXT,
        version TEXT,
        enabled INTEGER DEFAULT 1,
        installed_at INTEGER,
        granted_permissions TEXT DEFAULT '[]',
        state TEXT DEFAULT 'installed'
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS browser_downloads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT, path TEXT, filename TEXT, mime TEXT, bytes INTEGER,
        sha256 TEXT, mission_id TEXT, ts INTEGER, verified INTEGER DEFAULT 0
    )""")


@migration("005_skills")
def _m005(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS skills (
        skill_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        name TEXT,
        status TEXT DEFAULT 'candidate',
        scope TEXT DEFAULT 'user',
        scope_ref TEXT DEFAULT '',
        tags TEXT DEFAULT '[]',
        document TEXT NOT NULL,
        success_count INTEGER DEFAULT 0,
        failure_count INTEGER DEFAULT 0,
        created_at INTEGER,
        updated_at INTEGER,
        active INTEGER DEFAULT 0,
        PRIMARY KEY (skill_id, version)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_active ON skills(skill_id, active)")
    # Correction evidence: a user fix never silently rewrites an active skill (§5.18).
    conn.execute("""CREATE TABLE IF NOT EXISTS skill_corrections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_id TEXT NOT NULL,
        version INTEGER,
        step_id TEXT,
        failed_step TEXT,
        user_action TEXT,
        previous_state TEXT,
        resulting_state TEXT,
        detail TEXT,
        ts INTEGER
    )""")


@migration("006_devices")
def _m006(conn: sqlite3.Connection) -> None:
    # Device mesh (Phase 6): the registry is authoritative — `KNOWN_DEVICES` in the tool
    # catalogue is only a hint for the router, never truth.
    conn.execute("""CREATE TABLE IF NOT EXISTS devices (
        device_id TEXT PRIMARY KEY,
        name TEXT,
        type TEXT,
        trust_tier TEXT DEFAULT 'untrusted',
        state TEXT DEFAULT 'unpaired',
        capabilities TEXT DEFAULT '[]',
        document TEXT NOT NULL,
        fingerprint TEXT,
        address TEXT,
        transport TEXT DEFAULT 'tcp',
        paired_at INTEGER,
        paired_by TEXT,
        last_seen INTEGER,
        last_error TEXT,
        created_at INTEGER,
        updated_at INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_state ON devices(state)")
    # Pairing secrets live in the vault; only the reference and a fingerprint are stored here.
    conn.execute("""CREATE TABLE IF NOT EXISTS device_pairings (
        pairing_id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        token_ref TEXT NOT NULL,
        fingerprint TEXT,
        created_at INTEGER,
        revoked_at INTEGER,
        last_used INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pairings_device ON device_pairings(device_id)")
    # Every command and its result: the mesh must be auditable per device (§6).
    conn.execute("""CREATE TABLE IF NOT EXISTS device_commands (
        command_id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        capability TEXT NOT NULL,
        status TEXT DEFAULT 'queued',
        params TEXT DEFAULT '{}',
        result TEXT DEFAULT '{}',
        ok INTEGER DEFAULT 0,
        verified INTEGER DEFAULT 0,
        error_code TEXT,
        person_id TEXT,
        mission_id TEXT,
        trace_id TEXT,
        idempotency_key TEXT,
        issued_at INTEGER,
        expires_at INTEGER,
        completed_at INTEGER,
        latency_ms INTEGER DEFAULT 0
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_device_commands_device"
                 " ON device_commands(device_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_device_commands_idem"
                 " ON device_commands(idempotency_key)")


@migration("007_sync")
def _m007(conn: sqlite3.Connection) -> None:
    # Sync v1 (Phase 6 exit gate, golden #17): shared records with per-record logical versions,
    # so two devices can edit offline and converge deterministically.
    conn.execute("""CREATE TABLE IF NOT EXISTS sync_records (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        namespace TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        deleted INTEGER DEFAULT 0,
        device_id TEXT,
        updated_at_ms INTEGER,
        version INTEGER DEFAULT 1,
        updated_at INTEGER,
        UNIQUE(namespace, key)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_records_ns ON sync_records(namespace)")
    conn.execute("""CREATE TABLE IF NOT EXISTS sync_policies (
        namespace TEXT PRIMARY KEY,
        policy TEXT DEFAULT 'last-write-wins',
        updated_at INTEGER
    )""")
    # A conflict is only recorded when there is no causal order. Nothing is silently lost: both
    # sides are kept explicitly, so `manual` can hold the stored value until the owner decides.
    conn.execute("""CREATE TABLE IF NOT EXISTS sync_conflicts (
        conflict_id TEXT PRIMARY KEY,
        namespace TEXT NOT NULL,
        key TEXT NOT NULL,
        incoming TEXT,
        stored TEXT,
        chosen TEXT,
        policy TEXT,
        reason TEXT,
        resolved INTEGER DEFAULT 0,
        resolved_by TEXT,
        resolved_at INTEGER,
        ts INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_conflicts_open"
                 " ON sync_conflicts(resolved, namespace)")


@migration("008_perception")
def _m008(conn: sqlite3.Connection) -> None:
    # Perception (Phase 8). Zones and their sensing policies are owner settings, so they persist;
    # events are derived data with a retention window and a hard-delete path.
    conn.execute("""CREATE TABLE IF NOT EXISTS perception_zones (
        zone_id TEXT PRIMARY KEY,
        name TEXT,
        kind TEXT DEFAULT 'other',
        devices TEXT DEFAULT '[]',
        created_at INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS perception_policies (
        zone_id TEXT PRIMARY KEY,
        camera INTEGER DEFAULT 0,
        microphone TEXT DEFAULT 'wake_only',
        screen INTEGER DEFAULT 0,
        motion INTEGER DEFAULT 1,
        retention_s INTEGER DEFAULT 300,
        owner_enabled INTEGER DEFAULT 0,
        updated_at INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS perception_events (
        event_id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        zone_id TEXT,
        confidence REAL DEFAULT 0,
        source TEXT,
        evidence TEXT DEFAULT '{}',
        ts INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_perception_events_zone"
                 " ON perception_events(zone_id, ts)")


@migration("009_proactive")
def _m009(conn: sqlite3.Connection) -> None:
    # Proactivity (Phase 9). Every notification is stored with its score, its factors and the
    # event that caused it, so "why am I seeing this?" is answerable after the fact.
    conn.execute("""CREATE TABLE IF NOT EXISTS proactive_notifications (
        notification_id TEXT PRIMARY KEY,
        candidate_id TEXT,
        event_class TEXT NOT NULL,
        channel TEXT,
        outcome TEXT,
        title TEXT,
        detail TEXT,
        delivered INTEGER DEFAULT 0,
        suppressed INTEGER DEFAULT 0,
        suppression_reason TEXT,
        score REAL DEFAULT 0,
        factors TEXT DEFAULT '{}',
        zone_id TEXT,
        device_id TEXT,
        mission_id TEXT,
        source_event TEXT,
        ts INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_proactive_notifications_class"
                 " ON proactive_notifications(event_class, ts)")
    conn.execute("""CREATE TABLE IF NOT EXISTS proactive_quiet_hours (
        person_id TEXT NOT NULL DEFAULT '',
        zone_id TEXT NOT NULL DEFAULT '',
        start_hour INTEGER DEFAULT 22,
        end_hour INTEGER DEFAULT 7,
        enabled INTEGER DEFAULT 1,
        updated_at INTEGER,
        PRIMARY KEY (person_id, zone_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS proactive_preferences (
        event_class TEXT PRIMARY KEY,
        value REAL DEFAULT 0.5,
        updated_at INTEGER
    )""")


@migration("010_agents")
def _m010(conn: sqlite3.Connection) -> None:
    # Agent teams (Phase 10). Agents share a DAG, a mailbox, a blackboard and artifact
    # *references* — never one giant transcript.
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_definitions (
        agent_id TEXT PRIMARY KEY,
        profile_key TEXT,
        role TEXT,
        kind TEXT,
        version INTEGER DEFAULT 1,
        document TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        created_ms INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_definitions_profile"
                 " ON agent_definitions(profile_key, version)")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_tasks (
        task_id TEXT PRIMARY KEY,
        mission_id TEXT,
        objective TEXT,
        owner_agent TEXT,
        status TEXT,
        depends_on TEXT DEFAULT '[]',
        outputs TEXT DEFAULT '[]',
        attempt INTEGER DEFAULT 0,
        document TEXT,
        started_ms INTEGER,
        finished_ms INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_tasks_mission"
                 " ON agent_tasks(mission_id, status)")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_messages (
        message_id TEXT PRIMARY KEY,
        mission_id TEXT,
        from_agent TEXT,
        to_agent TEXT,
        type TEXT,
        subject TEXT,
        body TEXT,
        artifact_id TEXT,
        task_id TEXT,
        ts INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_blackboard (
        entry_id TEXT PRIMARY KEY,
        mission_id TEXT,
        kind TEXT,
        key TEXT,
        value TEXT,
        author TEXT,
        ts INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_blackboard_mission"
                 " ON agent_blackboard(mission_id, kind)")
    # Artifacts are referenced by id; contents live in one place with a hash (§10.7).
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_artifacts (
        artifact_id TEXT PRIMARY KEY,
        kind TEXT,
        name TEXT,
        path TEXT,
        sha256 TEXT,
        bytes INTEGER DEFAULT 0,
        version INTEGER DEFAULT 1,
        producer_agent TEXT,
        mission_id TEXT,
        task_id TEXT,
        dependencies TEXT DEFAULT '[]',
        content TEXT,
        created_ms INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_artifacts_mission"
                 " ON agent_artifacts(mission_id)")
    # Escalation must be measurable: every move to a stronger model is recorded with evidence.
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_escalations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_tier TEXT,
        to_tier TEXT,
        reason TEXT,
        evidence TEXT DEFAULT '{}',
        estimated_cost_usd REAL DEFAULT 0,
        ts INTEGER
    )""")
    # Structured experience only — never a raw conversation (§10.16).
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_experience (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_type TEXT,
        role TEXT,
        strategy TEXT,
        provider TEXT,
        tools TEXT DEFAULT '[]',
        success INTEGER DEFAULT 0,
        failures TEXT DEFAULT '[]',
        latency_ms INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0,
        mission_id TEXT,
        ts INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_experience_type"
                 " ON agent_experience(task_type, success)")


@migration("011_ops")
def _m011(conn: sqlite3.Connection) -> None:
    # Operator observability & control surface (Phase 11). Per-mission outcome snapshots feed
    # the experience->improvement loop (§10.16) that Phase 12 self-improvement consumes.
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_mission_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mission_id TEXT,
        objective TEXT,
        final_status TEXT,
        team_size INTEGER DEFAULT 0,
        tasks_total INTEGER DEFAULT 0,
        tasks_done INTEGER DEFAULT 0,
        tasks_failed INTEGER DEFAULT 0,
        providers TEXT DEFAULT '[]',
        failovers INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0,
        duration_ms INTEGER DEFAULT 0,
        orphan_count INTEGER DEFAULT 0,
        experience_records INTEGER DEFAULT 0,
        snapshot TEXT DEFAULT '{}',
        ts INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_outcomes_mission"
                 " ON agent_mission_outcomes(mission_id)")


@migration("012_eval")
def _m012(conn: sqlite3.Connection) -> None:
    # Evaluation / dev lab (Phase 12.2) — capability adapted from Qwen-AgentWorld.
    # Runs are kept so a later change can be compared against a baseline and regressions
    # detected instead of being discovered in production.
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_eval_runs (
        run_id TEXT PRIMARY KEY,
        suite TEXT,
        started_ms INTEGER,
        finished_ms INTEGER,
        scenarios INTEGER DEFAULT 0,
        passed INTEGER DEFAULT 0,
        mean_score REAL DEFAULT 0,
        note TEXT DEFAULT ''
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_eval_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        scenario_id TEXT,
        domain TEXT,
        passed INTEGER DEFAULT 0,
        score REAL DEFAULT 0,
        dimensions TEXT DEFAULT '{}',
        duration_ms INTEGER DEFAULT 0,
        detail TEXT DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_eval_results_run"
                 " ON agent_eval_results(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_eval_results_scenario"
                 " ON agent_eval_results(scenario_id)")


@migration("013_metrics")
def _m013(conn: sqlite3.Connection) -> None:
    # Wrong-action rate (Phase 14 exit gate). Without this the gate is unmeasurable — you cannot
    # hold a release to "wrong-action rate within target" with nothing counting wrong actions.
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_action_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER,
        capability TEXT,
        ok INTEGER DEFAULT 0,
        verified INTEGER DEFAULT 0,
        kind TEXT,
        detail TEXT DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_action_outcomes_ts"
                 " ON agent_action_outcomes(ts)")


@migration("014_channels")
def _m014(conn: sqlite3.Connection) -> None:
    """Channel ingress + session continuity (P5, OpenClaw gap).

    Without this a conversation lives only in the browser tab that started it:
    the desktop UI minted its own session_id and the backend merely threaded the
    string through, so nothing survived a restart and no second transport could
    be added without special-casing. Sessions are keyed by (channel,
    external_key) so the same person resumes the same conversation.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS channel_sessions (
        session_id TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        external_key TEXT NOT NULL,
        person_id TEXT DEFAULT '',
        title TEXT DEFAULT '',
        created_at INTEGER,
        updated_at INTEGER,
        message_count INTEGER DEFAULT 0
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chan_sess_key"
                 " ON channel_sessions(channel, external_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chan_sess_updated"
                 " ON channel_sessions(updated_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS channel_messages (
        message_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        text TEXT NOT NULL,
        metadata TEXT DEFAULT '{}',
        created_at INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chan_msg_session"
                 " ON channel_messages(session_id, created_at)")


@migration("015_usermodel")
def _m015(conn: sqlite3.Connection) -> None:
    """Cross-session user model (P5, Hermes gap).

    Evidence-weighted facts about a person that persist and deepen across
    sessions. Superseded rows are kept (never silently overwritten) so the
    model's history stays auditable and a correction is traceable.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS user_model_facts (
        fact_id TEXT PRIMARY KEY,
        person_id TEXT NOT NULL,
        trait TEXT NOT NULL,
        value TEXT NOT NULL,
        confidence REAL DEFAULT 0.55,
        support INTEGER DEFAULT 1,
        source TEXT DEFAULT 'conversation',
        evidence TEXT DEFAULT '',
        last_evidence TEXT DEFAULT '',
        session_id TEXT DEFAULT '',
        last_session TEXT DEFAULT '',
        created_at INTEGER,
        updated_at INTEGER,
        superseded_at INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umf_person"
                 " ON user_model_facts(person_id, trait)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umf_active"
                 " ON user_model_facts(person_id, superseded_at)")


@migration("016_experience_bank")
def _m016(conn: sqlite3.Connection) -> None:
    """Trajectories + textual experience bank (P5, Youtu-Agent gap).

    ExperienceStore already records per-task outcomes, but GENIE could not
    compare a GROUP of rollouts and turn the difference into a reusable
    natural-language lesson (Training-Free GRPO). These tables hold the
    rollouts and the derived lessons.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_trajectories (
        trajectory_id TEXT PRIMARY KEY,
        task_type TEXT NOT NULL,
        role TEXT DEFAULT '',
        strategy TEXT DEFAULT '',
        provider TEXT DEFAULT '',
        tools TEXT DEFAULT '[]',
        steps TEXT DEFAULT '[]',
        step_count INTEGER DEFAULT 0,
        success INTEGER DEFAULT 0,
        failures TEXT DEFAULT '[]',
        summary TEXT DEFAULT '',
        mission_id TEXT DEFAULT '',
        latency_ms INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0,
        ts INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trj_task"
                 " ON agent_trajectories(task_type, ts)")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_experience_bank (
        lesson_id TEXT PRIMARY KEY,
        task_type TEXT NOT NULL,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        support INTEGER DEFAULT 1,
        confidence REAL DEFAULT 0.0,
        updated_at INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expbank_task"
                 " ON agent_experience_bank(task_type, support)")


@migration("017_specs")
def _m017(conn: sqlite3.Connection) -> None:
    """Gated spec pipeline (P5, Spec Kit gap).

    requirements -> plan -> tasks -> implement -> validate, with each phase
    leaving an artifact and refusing to advance while a requirement is
    uncovered. Validation results are appended, never overwritten, so the
    history of how a spec converged stays auditable.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS spec_runs (
        spec_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        mission_id TEXT DEFAULT '',
        phase TEXT DEFAULT 'specify',
        constitution TEXT DEFAULT '[]',
        created_at INTEGER,
        updated_at INTEGER,
        validated_at INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS spec_requirements (
        requirement_id TEXT PRIMARY KEY,
        spec_id TEXT NOT NULL,
        text TEXT NOT NULL,
        rationale TEXT DEFAULT '',
        created_at INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_specreq_spec"
                 " ON spec_requirements(spec_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS spec_tasks (
        task_id TEXT PRIMARY KEY,
        spec_id TEXT NOT NULL,
        text TEXT NOT NULL,
        requirement_ids TEXT DEFAULT '[]',
        capability TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        outcome TEXT DEFAULT '',
        evidence TEXT DEFAULT '',
        created_at INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_spectask_spec"
                 " ON spec_tasks(spec_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS spec_artifacts (
        artifact_id TEXT PRIMARY KEY,
        spec_id TEXT NOT NULL,
        phase TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS spec_validations (
        validation_id TEXT PRIMARY KEY,
        spec_id TEXT NOT NULL,
        ok INTEGER DEFAULT 0,
        detail TEXT DEFAULT '{}',
        ts INTEGER
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_specval_spec"
                 " ON spec_validations(spec_id, ts)")


@migration("018_knowledge")
def _m018(conn: sqlite3.Connection) -> None:
    """Imported knowledge sources (distinct from personal memory).

    Per-file index entries reuse the existing workspace_index table; this table
    holds only source-level provenance: who imported it, when, how many items,
    and the last error if the import did not complete. A source that failed to
    index keeps its error instead of silently reporting zero items.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_sources (
        source_id TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        kind TEXT DEFAULT 'directory',
        item_count INTEGER DEFAULT 0,
        added_by TEXT DEFAULT 'owner',
        added_at INTEGER,
        last_error TEXT DEFAULT ''
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ksource_path"
                 " ON knowledge_sources(path)")


@migration("019_mission_schedule")
def _m019(conn: sqlite3.Connection) -> None:
    """Point 6 — a Mission may carry a recurrence/schedule hint.

    Additive and nullable, so every existing row keeps working unchanged.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(missions)")}
    if "schedule" not in cols:
        conn.execute("ALTER TABLE missions ADD COLUMN schedule TEXT DEFAULT ''")


@migration("020_mission_execution")
def _m020(conn: sqlite3.Connection) -> None:
    """Pass 3 — durable mission execution.

    * mission_steps gains the DAG edges + retry/timing/evidence columns so the
      plan survives exit/relaunch and a step is never re-run blindly.
    * missions gains continuous/next_run/last_run for scheduled + continuous work.
    * mission_schedule holds a NORMALIZED schedule (not just the NL hint).
    """
    step_cols = {r["name"] for r in conn.execute("PRAGMA table_info(mission_steps)")}
    for name, ddl in (
        ("depends_on", "TEXT DEFAULT '[]'"),
        ("objective", "TEXT DEFAULT ''"),
        ("attempt", "INTEGER DEFAULT 0"),
        ("max_attempts", "INTEGER DEFAULT 2"),
        ("required_role", "TEXT DEFAULT 'worker'"),
        ("completion_criteria", "TEXT DEFAULT ''"),
        ("started_ms", "INTEGER DEFAULT 0"),
        ("finished_ms", "INTEGER DEFAULT 0"),
        ("provider_used", "TEXT DEFAULT ''"),
        ("evidence", "TEXT DEFAULT '{}'"),
        ("idempotency_key", "TEXT DEFAULT ''"),
    ):
        if name not in step_cols:
            conn.execute(f"ALTER TABLE mission_steps ADD COLUMN {name} {ddl}")

    mission_cols = {r["name"] for r in conn.execute("PRAGMA table_info(missions)")}
    for name, ddl in (
        ("continuous", "INTEGER DEFAULT 0"),
        ("next_run_ms", "INTEGER DEFAULT 0"),
        ("last_run_ms", "INTEGER DEFAULT 0"),
    ):
        if name not in mission_cols:
            conn.execute(f"ALTER TABLE missions ADD COLUMN {name} {ddl}")

    conn.execute("""CREATE TABLE IF NOT EXISTS mission_schedule (
        mission_id TEXT PRIMARY KEY,
        spec TEXT DEFAULT '{}',
        next_run_ms INTEGER DEFAULT 0,
        last_run_ms INTEGER DEFAULT 0,
        enabled INTEGER DEFAULT 1,
        catch_up TEXT DEFAULT 'once',
        missed_count INTEGER DEFAULT 0,
        created_at INTEGER,
        updated_at INTEGER
    )""")


@migration("021_provider_runtime")
def _m021(conn: sqlite3.Connection) -> None:
    """Backfill provider_runtime on databases created before it existed.

    provider_runtime lives inside 001_core, but migrations never re-run once
    applied. Any database created before the table was added therefore stayed
    without it, and every provider listing failed with
    "no such table: provider_runtime". This migration is additive and safe on
    both old and new databases.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS provider_runtime (
        provider_id TEXT PRIMARY KEY,
        status TEXT DEFAULT 'ready',
        detail TEXT DEFAULT '',
        since_ms INTEGER DEFAULT 0,
        until_ms INTEGER DEFAULT 0
    )""")


@migration("022_session_turns")
def _m022(conn: sqlite3.Connection) -> None:
    """Owner session continuity (Phase 0 closure).

    Recent exact turns for an owner session are persisted (bounded) so a
    backend restart does not lose conversational context. This is SEPARATE from
    the long-term MemoryService: it is ephemeral session state, not a permanent
    memory. Older turns beyond the bound are summarised into a single
    checkpoint row rather than retained verbatim.

    Keyed by session_id (canonical owner session = "owner").
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS session_turns (
        turn_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        text TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        seq INTEGER NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_turns_session"
                 " ON session_turns(session_id, seq)")
    conn.execute("""CREATE TABLE IF NOT EXISTS session_checkpoints (
        session_id TEXT PRIMARY KEY,
        summary TEXT NOT NULL,
        updated_at INTEGER NOT NULL,
        turn_seq INTEGER NOT NULL
    )""")


@migration("041_local_observations")
def _local_observations(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS local_observations ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,"
                 "kind TEXT NOT NULL, payload BLOB NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_observations_time ON local_observations(ts)")


@migration("042_conversation_archive")
def _conversation_archive(conn):
    conn.execute("CREATE TABLE conversation_messages ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,"
                 "role TEXT NOT NULL, text TEXT NOT NULL, created_at INTEGER NOT NULL)")
    conn.execute("CREATE INDEX idx_conversation_session ON conversation_messages(session_id, id)")
    conn.execute("INSERT INTO conversation_messages(session_id, role, text, created_at) "
                 "SELECT session_id, role, text, created_at FROM session_turns ORDER BY created_at, seq")


_DB: Database | None = None


def get_db(path: str | Path | None = None, wal: bool = True) -> Database:
    global _DB
    if _DB is None:
        if path is None:
            from .config import get_config
            path = get_config().db_path
        _DB = Database(path, wal=wal)
    return _DB


def reset_db_for_tests(path: str | Path) -> Database:
    """Fresh DB for tests (never touches the real data dir)."""
    global _DB
    p = Path(path)
    if p.exists():
        p.unlink()
    for suffix in ("-wal", "-shm"):
        if Path(str(p) + suffix).exists():
            Path(str(p) + suffix).unlink()
    _DB = Database(p)
    return _DB
