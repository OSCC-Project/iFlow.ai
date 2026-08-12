#!/usr/bin/env python3
"""state.py —— State 模块: 接收 SnapshotPackage 并持久化 (v1.0)"""
import json, os, shutil, sqlite3, sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Backward compat
try:
    from adapter.contract import SnapshotPackage, SimError, TracePoint, ExecutionTraceEntry
    TracePoint  # make pyflakes happy
except ImportError:
    from adapter.contract import SnapshotPackage, SimError


class StateStore:
    def __init__(self, base_dir=None):
        base_dir = base_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "state")
        self.base_dir = Path(base_dir); self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "state.db"
        self.snapshots_dir = self.base_dir / "snapshots"; self.snapshots_dir.mkdir(exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    snapshot_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    parent_snapshot_id TEXT DEFAULT '',
                    tool TEXT NOT NULL, tool_version TEXT, adapter_version TEXT,
                    design_name TEXT, design_type TEXT,
                    stage TEXT, step INT DEFAULT 0, timestamp TEXT NOT NULL,
                    schema_version TEXT DEFAULT '1.0',
                    snapshot_type TEXT DEFAULT 'CHECKPOINT',
                    observation_level TEXT DEFAULT '1',
                    label TEXT, created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS capabilities (
                    snapshot_id TEXT PRIMARY KEY REFERENCES runs(snapshot_id),
                    adapter TEXT, artifact INT DEFAULT 1, metric INT DEFAULT 1,
                    object_delta INT DEFAULT 0, execution_trace INT DEFAULT 0,
                    waveform INT DEFAULT 0, extras TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS observation_contexts (
                    snapshot_id TEXT PRIMARY KEY REFERENCES runs(snapshot_id),
                    stage TEXT, operation TEXT, command TEXT,
                    parameters TEXT DEFAULT '{}', duration_ms REAL DEFAULT 0,
                    trigger TEXT, work_dir TEXT,
                    metrics_snapshot TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS design_infos (
                    snapshot_id TEXT PRIMARY KEY REFERENCES runs(snapshot_id),
                    name TEXT, technology TEXT, top_module TEXT
                );
                CREATE TABLE IF NOT EXISTS design_objects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT REFERENCES runs(snapshot_id),
                    obj_id TEXT, type TEXT, master TEXT DEFAULT '',
                    properties TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT REFERENCES runs(snapshot_id),
                    source TEXT NOT NULL, metric_name TEXT NOT NULL, value REAL,
                    UNIQUE(snapshot_id, source, metric_name)
                );
                CREATE TABLE IF NOT EXISTS constraints (
                    snapshot_id TEXT REFERENCES runs(snapshot_id),
                    key TEXT NOT NULL, value TEXT, PRIMARY KEY(snapshot_id, key)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id TEXT, snapshot_id TEXT REFERENCES runs(snapshot_id),
                    logical_name TEXT, type TEXT DEFAULT 'file',
                    source_uri TEXT, size INT DEFAULT 0, checksum TEXT,
                    producer TEXT, stage TEXT DEFAULT '', depends_on TEXT DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS execution_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT REFERENCES runs(snapshot_id),
                    operation TEXT, iteration INT DEFAULT 0, command TEXT,
                    parameters TEXT DEFAULT '{}', duration_ms REAL DEFAULT 0,
                    trigger TEXT, metrics_snapshot TEXT DEFAULT '{}',
                    checkpoint TEXT, timestamp TEXT
                );
            """)
            conn.commit()

    def list_all(self, limit=50):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT snapshot_id,run_id,tool,stage,snapshot_type,observation_level,design_name,created_at FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, sid):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM runs WHERE snapshot_id LIKE ?||'%' OR run_id LIKE ?||'%' LIMIT 1", (sid, sid)).fetchone()
        return self._enrich(dict(row), row["snapshot_id"]) if row else None

    def latest(self, circuit):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT r.* FROM runs r JOIN design_infos d ON r.snapshot_id=d.snapshot_id WHERE d.name=? ORDER BY r.timestamp DESC LIMIT 1", (circuit,)
            ).fetchone()
        return self._enrich(dict(row), row["snapshot_id"]) if row else None

    def _enrich(self, row, sid):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            for tbl in ["capabilities","observation_contexts","design_infos"]:
                r = conn.execute(f"SELECT * FROM {tbl} WHERE snapshot_id=?", (sid,)).fetchone()
                if r: row[tbl] = dict(r)
            row["metrics"] = {}
            for s,n,v in conn.execute("SELECT source,metric_name,value FROM metrics WHERE snapshot_id=?",(sid,)).fetchall():
                row["metrics"].setdefault(s,{})[n]=v
            row["constraints"] = dict(conn.execute("SELECT key,value FROM constraints WHERE snapshot_id=?",(sid,)).fetchall())
            row["artifact_manifest"] = [dict(a) for a in conn.execute("SELECT * FROM artifacts WHERE snapshot_id=?",(sid,)).fetchall()]
            row["execution_trace"] = [dict(t) for t in conn.execute("SELECT * FROM execution_traces WHERE snapshot_id=?",(sid,)).fetchall()]
        return row

    def stats(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            total = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            by_tool = dict(conn.execute("SELECT tool,COUNT(*) FROM runs GROUP BY tool").fetchall())
            by_obs = dict(conn.execute("SELECT observation_level,COUNT(*) FROM runs GROUP BY observation_level").fetchall())
            t_art = conn.execute("SELECT COUNT(*),COALESCE(SUM(size),0) FROM artifacts").fetchone()
        return {"total_runs": total, "by_tool": by_tool, "by_observation_level": by_obs,
                "total_artifacts": t_art[0], "total_artifact_bytes": t_art[1],
                "db_path": str(self.db_path)}


class SnapshotReceiver:
    def __init__(self, store=None):
        self.store = store or StateStore()

    def submit_snapshot(self, pkg):
        h, cap, ctx, dt = pkg.header, pkg.capability, pkg.observation_context, pkg.digital_twin
        sid = h.snapshot_id

        with sqlite3.connect(str(self.store.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, h.run_id, h.parent_snapshot_id, h.tool, h.tool_version, h.adapter_version,
                 getattr(h,'design_name',''), getattr(h,'design_type',''),
                 h.stage, h.step, h.timestamp, getattr(h,'schema_version','1.0'),
                 h.snapshot_type, h.observation_level, "", None)
            )
            conn.execute("INSERT OR REPLACE INTO capabilities VALUES (?,?,?,?,?,?,?,?)",
                         (sid, cap.adapter, int(cap.artifact), int(cap.metric),
                          int(getattr(cap,'object_delta',False)), int(getattr(cap,'execution_trace',False)),
                          int(getattr(cap,'waveform',False)), json.dumps(getattr(cap,'extras',{}))))
            conn.execute("INSERT OR REPLACE INTO observation_contexts VALUES (?,?,?,?,?,?,?,?,?)",
                         (sid, getattr(ctx,'stage',''), ctx.operation, ctx.command,
                          json.dumps(getattr(ctx,'parameters',{})), getattr(ctx,'duration_ms',0.0),
                          ctx.trigger, getattr(ctx,'work_dir',''), json.dumps(getattr(ctx,'metrics_snapshot',{}))))
            d = getattr(dt,'design',None)
            if d:
                conn.execute("INSERT OR REPLACE INTO design_infos VALUES (?,?,?,?)",
                             (sid, getattr(d,'name',''), getattr(d,'technology',''), getattr(d,'top','')))
            for o in (getattr(dt,'objects',[]) or []):
                conn.execute("INSERT OR REPLACE INTO design_objects VALUES (?,?,?,?,?,?)",
                             (None, sid, getattr(o,'id',''), getattr(o,'type',''),
                              getattr(o,'master',''), json.dumps(getattr(o,'properties',{}))))
            metrics = getattr(dt,'metrics',{}) or {}
            for src, vals in metrics.items():
                for mn, mv in (vals.items() if isinstance(vals,dict) else [(src,vals)]):
                    conn.execute("INSERT OR REPLACE INTO metrics VALUES (?,?,?,?,?)",
                                 (None, sid, src, mn, None if isinstance(mv,float) and mv!=mv else mv))
            for k,v in (getattr(dt,'constraints',{}) or {}).items():
                conn.execute("INSERT OR REPLACE INTO constraints VALUES (?,?,?)", (sid, k, str(v)))
            for a in (pkg.artifact_manifest or []):
                conn.execute("INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (None, getattr(a,'artifact_id',''), sid, getattr(a,'logical_name',''),
                              getattr(a,'type','file'), getattr(a,'source_uri',''),
                              getattr(a,'size',0), getattr(a,'checksum',''),
                              getattr(a,'producer',''), getattr(a,'stage',''),
                              json.dumps(getattr(a,'depends_on',[]))))
            for et in (pkg.execution_trace or []):
                conn.execute("INSERT OR REPLACE INTO execution_traces VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (None, sid, getattr(et,'operation',''), getattr(et,'iteration',0),
                              getattr(et,'command',''), json.dumps(getattr(et,'parameters',{})),
                              getattr(et,'duration_ms',0.0), getattr(et,'trigger',''),
                              json.dumps(getattr(et,'metrics_snapshot',{})),
                              getattr(et,'checkpoint',''), getattr(et,'timestamp','')))
            conn.commit()

        snap_dir = self.store.snapshots_dir / sid
        snap_dir.mkdir(exist_ok=True)
        (snap_dir / "snapshot.json").write_text(
            json.dumps(asdict(pkg), indent=2, ensure_ascii=False,
                       default=lambda o: None if isinstance(o,float) and o!=o else str(o)))
        art_dir = snap_dir / "artifacts"; art_dir.mkdir(exist_ok=True)
        for a in (pkg.artifact_manifest or []):
            uri = getattr(a,'source_uri','')
            if uri and os.path.exists(uri):
                dst = art_dir / os.path.basename(uri)
                if not dst.exists(): shutil.copy2(uri, dst)

        n_m = len(metrics); n_a = len(pkg.artifact_manifest or [])
        print(f"[State] ✅ submitted  snap={sid[:16]}  tool={h.tool}  level={h.observation_level}  "
              f"type={h.snapshot_type}  metrics={n_m}  artifacts={n_a}")
        return sid

    def receive(self, result, label=""):
        if isinstance(result, SimError):
            print(f"[State] ❌ [{result.type}] {result.likely_cause[:80]}"); return None
        return self.submit_snapshot(result)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    store = StateStore()
    if cmd == "list":
        for r in store.list_all(20):
            print(f"{r.get('snapshot_id',r.get('run_id',''))[:20]:20s} [{r.get('tool',''):10s}] "
                  f"L{r.get('observation_level','')} {r.get('snapshot_type',''):12s} {r.get('design_name','')}")
    elif cmd == "show":
        run = store.get(sys.argv[2]) if len(sys.argv)>2 else None
        if run: print(json.dumps(run, indent=2, ensure_ascii=False, default=str))
    elif cmd == "stats":
        s = store.stats()
        print(f"Total runs: {s['total_runs']} | By tool: {s['by_tool']} | By level: {s['by_observation_level']}")
        print(f"Artifacts: {s['total_artifacts']} | Size: {s['total_artifact_bytes']:,} bytes")
