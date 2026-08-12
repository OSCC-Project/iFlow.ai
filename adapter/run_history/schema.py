"""
run_history/schema.py — SQLite schema for run history

One table (run_history) records every complete flow execution:
user input → generated flow → execution result.

This is the knowledge base that powers FlowRecommender.
"""
import sqlite3, os, json
from pathlib import Path


DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "outputs", "state", "run_history.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS run_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT DEFAULT (datetime('now')),

    -- User input (CLI format, unchanged)
    design        TEXT NOT NULL,
    technology    TEXT NOT NULL DEFAULT 'Nangate45',
    requirements  TEXT NOT NULL DEFAULT '[]',
    goals_json    TEXT NOT NULL DEFAULT '{}',
    fast_mode     INTEGER DEFAULT 0,
    rtl_path      TEXT DEFAULT '',

    -- RTL analysis (extracted before run)
    gate_count    INTEGER DEFAULT 0,
    top_module    TEXT DEFAULT '',

    -- Run metadata
    run_type      TEXT NOT NULL DEFAULT 'demo',   -- 'demo' | 'final'
    parent_run_id INTEGER DEFAULT NULL,           -- final run's demo parent

    -- Generated flow (ComposedFlow → JSON)
    flow_name     TEXT DEFAULT '',
    flow_phase    TEXT DEFAULT 'explore',
    flow_steps_json TEXT DEFAULT '[]',
    flow_warnings_json TEXT DEFAULT '[]',

    -- Execution result (SnapshotPackage → flattened)
    metrics_json     TEXT DEFAULT '{}',
    passed           INTEGER DEFAULT 0,
    duration_ms      INTEGER DEFAULT 0,
    error_msg        TEXT DEFAULT ''
);
"""


def init_db():
    db_dir = os.path.dirname(DB_PATH)
    os.makedirs(db_dir, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()


def get_conn():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
