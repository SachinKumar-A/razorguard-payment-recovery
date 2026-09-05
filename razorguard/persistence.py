"""Durable state, so a restart is not a three-hour blind spot.

The control plane needs roughly three hours of history before its baselines mean
anything. Held in memory, that made every restart - a deploy, a crash, a node
reschedule - a window in which the system was awake but blind, and worse, one
where it could not tell it was blind. That is the single most dangerous failure
mode a monitoring system can have.

What is persisted, and what is not
----------------------------------
Persisted: the recent observation stream, the routing table, open diversions,
and the audit ledger.

Not persisted: the detector's internal state, the health tracker's windows, or
any derived statistic. Those are *rebuilt by replay* on boot - the stored
observations are fed back through the same code that consumed them live, with
acting suppressed. Serialising a detector's internals would mean maintaining a
second representation of every statistical structure and a migration path for
each; replay needs neither, and it guarantees the restored state is exactly the
state the live path would have produced.

Retention is bounded to what the baselines actually need, because this store
exists to survive a restart, not to be a data warehouse.

Durability
----------
SQLite in WAL mode. No server to run, crash-safe, and the whole state is one
file you can copy. The audit ledger is append-only here as it is in memory: rows
are inserted, never updated or deleted.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Dict, List, Optional, Tuple

from .audit import AuditEvent
from .simulator import Observation

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    minute       INTEGER NOT NULL,
    slice_key    TEXT    NOT NULL,
    method       TEXT    NOT NULL,
    attempts     INTEGER NOT NULL,
    successes    INTEGER NOT NULL,
    ticket       REAL    NOT NULL,
    PRIMARY KEY (minute, slice_key)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS observations_minute ON observations(minute);

CREATE TABLE IF NOT EXISTS audit_events (
    seq       INTEGER PRIMARY KEY,
    minute    INTEGER NOT NULL,
    kind      TEXT    NOT NULL,
    subject   TEXT    NOT NULL,
    summary   TEXT    NOT NULL,
    rule      TEXT,
    evidence  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_kind ON audit_events(kind);

CREATE TABLE IF NOT EXISTS routing (
    method   TEXT NOT NULL,
    issuer   TEXT NOT NULL,
    gateway  TEXT NOT NULL,
    weight   REAL NOT NULL,
    PRIMARY KEY (method, issuer, gateway)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS diversions (
    method         TEXT    NOT NULL,
    issuer         TEXT    NOT NULL,
    source         TEXT    NOT NULL,
    target         TEXT    NOT NULL,
    opened_min     INTEGER NOT NULL,
    shifted        REAL    NOT NULL,
    healthy_streak INTEGER NOT NULL,
    restoring      INTEGER NOT NULL,
    PRIMARY KEY (method, issuer)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Exactly one row, id = 1. See Lease.
CREATE TABLE IF NOT EXISTS lease (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    holder     TEXT    NOT NULL,
    expires_at REAL    NOT NULL,
    fenced     INTEGER NOT NULL DEFAULT 0
);
"""


class Store:
    """SQLite-backed state for one control plane."""

    def __init__(self, path: str, retain_minutes: int = 400):
        #: Kept a little above the 180 minutes of history the health tracker
        #: holds, so a restart restores a full window rather than a partial one.
        self.retain_minutes = retain_minutes
        self.path = path
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    # -- meta -------------------------------------------------------------

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?",
                              (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)))

    @property
    def minute(self) -> int:
        return int(self.get_meta("minute", "0") or 0)

    # -- observations ------------------------------------------------------

    def save_observations(self, observations: List[Observation]) -> None:
        if not observations:
            return
        # A minute can legitimately be re-written if a producer resends it
        # before the loop advances; the primary key makes that idempotent
        # rather than duplicating the slice.
        self.db.executemany(
            "INSERT INTO observations(minute, slice_key, method, attempts, "
            "successes, ticket) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(minute, slice_key) DO UPDATE SET "
            "attempts = excluded.attempts, successes = excluded.successes",
            [(o.minute, o.slice_key, o.method, o.attempts, o.successes,
              o.avg_ticket_inr) for o in observations])

    def prune(self, before_minute: int) -> int:
        cur = self.db.execute("DELETE FROM observations WHERE minute < ?",
                              (before_minute,))
        return cur.rowcount

    def recent_observations(self, minutes: int) -> List[Observation]:
        row = self.db.execute("SELECT MAX(minute) AS m FROM observations").fetchone()
        if row is None or row["m"] is None:
            return []
        floor = row["m"] - minutes
        rows = self.db.execute(
            "SELECT * FROM observations WHERE minute > ? ORDER BY minute ASC",
            (floor,)).fetchall()
        return [Observation(minute=r["minute"], slice_key=r["slice_key"],
                            method=r["method"], attempts=r["attempts"],
                            successes=r["successes"],
                            avg_ticket_inr=r["ticket"]) for r in rows]

    # -- audit -------------------------------------------------------------

    def save_audit(self, events: List[AuditEvent]) -> None:
        if not events:
            return
        self.db.executemany(
            "INSERT OR IGNORE INTO audit_events(seq, minute, kind, subject, "
            "summary, rule, evidence) VALUES(?,?,?,?,?,?,?)",
            [(e.seq, e.minute, e.kind, e.subject, e.summary, e.rule,
              json.dumps(e.evidence, default=str)) for e in events])

    def audit_tail(self, limit: int = 100,
                   kind: Optional[str] = None) -> List[Dict]:
        sql = "SELECT * FROM audit_events"
        params: Tuple = ()
        if kind:
            sql += " WHERE kind = ?"
            params = (kind,)
        sql += " ORDER BY seq DESC LIMIT ?"
        rows = self.db.execute(sql, params + (limit,)).fetchall()
        return [{"seq": r["seq"], "minute": r["minute"], "kind": r["kind"],
                 "subject": r["subject"], "summary": r["summary"],
                 "rule": r["rule"], "evidence": json.loads(r["evidence"])}
                for r in reversed(rows)]

    @property
    def max_audit_seq(self) -> int:
        row = self.db.execute("SELECT MAX(seq) AS s FROM audit_events").fetchone()
        return int(row["s"] or 0)

    # -- routing and diversions -------------------------------------------

    def save_routing(self, routing) -> None:
        rows = [(m, i, g, w)
                for (m, i), weights in routing.current.items()
                for g, w in weights.items()]
        self.db.executemany(
            "INSERT INTO routing(method, issuer, gateway, weight) "
            "VALUES(?,?,?,?) ON CONFLICT(method, issuer, gateway) "
            "DO UPDATE SET weight = excluded.weight", rows)

    def load_routing_into(self, routing) -> int:
        rows = self.db.execute("SELECT * FROM routing").fetchall()
        restored = 0
        for r in rows:
            key = (r["method"], r["issuer"])
            if key in routing.current and r["gateway"] in routing.current[key]:
                routing.current[key][r["gateway"]] = r["weight"]
                restored += 1
        return restored

    def save_diversions(self, diversions: Dict) -> None:
        self.db.execute("DELETE FROM diversions")
        self.db.executemany(
            "INSERT INTO diversions(method, issuer, source, target, "
            "opened_min, shifted, healthy_streak, restoring) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [(d.method, d.issuer, d.source, d.target, d.opened_min,
              d.shifted, d.healthy_streak, int(d.restoring))
             for d in diversions.values()])

    def load_diversions(self) -> List[Dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM diversions")]

    def commit(self) -> None:
        self.db.commit()


class Lease:
    """One active instance at a time, with automatic failover.

    The control plane holds all its state in one process and its decisions have
    to be globally consistent: two instances each seeing half the ingest stream
    would each conclude the fleet was half its real size, and both would act on
    it. So replicas are for *failover*, not for sharing load - exactly one is
    ever active, and the others wait.

    A lease in the shared database decides which. The holder renews it every
    tick; if the holder dies, the lease expires and a standby takes over,
    warming from the same stored history the dead one was writing.

    `fenced` is the guard against the classic failure: a process that stalls
    long enough to lose its lease, then wakes up and carries on acting as
    though it still holds it. Every renewal checks the holder is still us, and
    a renewal that finds otherwise returns False - the caller must stop acting
    immediately rather than finish the tick it was in the middle of.

    Note the honest limit: with SQLite this works for replicas sharing one
    volume. Across machines the database has to move to something with real
    multi-writer semantics, and that is a genuine piece of work rather than a
    configuration change.
    """

    def __init__(self, store: "Store", ttl_seconds: float = 90.0,
                 holder: Optional[str] = None):
        self.store = store
        self.ttl = ttl_seconds
        self.holder = holder or f"{os.uname().nodename if hasattr(os, 'uname') else os.environ.get('HOSTNAME', 'local')}-{uuid.uuid4().hex[:8]}"
        self.is_active = False

    def _row(self):
        return self.store.db.execute(
            "SELECT holder, expires_at FROM lease WHERE id = 1").fetchone()

    def try_acquire(self, now: Optional[float] = None) -> bool:
        """Take the lease if it is free, expired, or already ours."""
        current = time.time() if now is None else now
        db = self.store.db
        try:
            db.execute("BEGIN IMMEDIATE")
            row = self._row()
            if row is not None and row["holder"] != self.holder \
                    and row["expires_at"] > current:
                db.execute("ROLLBACK")
                self.is_active = False
                return False
            db.execute(
                "INSERT INTO lease(id, holder, expires_at) VALUES(1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET holder = excluded.holder, "
                "expires_at = excluded.expires_at",
                (self.holder, current + self.ttl))
            db.execute("COMMIT")
        except sqlite3.OperationalError:
            # Another replica is mid-transaction. Losing a race is normal; the
            # next attempt will settle it.
            self.is_active = False
            return False
        self.is_active = True
        return True

    def renew(self, now: Optional[float] = None) -> bool:
        """Extend our lease. False means we lost it and must stop acting."""
        current = time.time() if now is None else now
        db = self.store.db
        try:
            db.execute("BEGIN IMMEDIATE")
            row = self._row()
            if row is None or row["holder"] != self.holder:
                db.execute("ROLLBACK")
                self.is_active = False
                return False
            db.execute("UPDATE lease SET expires_at = ? WHERE id = 1",
                       (current + self.ttl,))
            db.execute("COMMIT")
        except sqlite3.OperationalError:
            self.is_active = False
            return False
        self.is_active = True
        return True

    def release(self) -> None:
        """Give the lease up on a clean shutdown, so failover is immediate."""
        try:
            self.store.db.execute(
                "UPDATE lease SET expires_at = 0 WHERE id = 1 AND holder = ?",
                (self.holder,))
            self.store.db.commit()
        except sqlite3.Error:
            pass
        self.is_active = False

    def status(self) -> Dict[str, object]:
        row = self._row()
        if row is None:
            return {"holder": None, "active": False, "self": self.holder}
        return {
            "holder": row["holder"],
            "active": row["holder"] == self.holder
                      and row["expires_at"] > time.time(),
            "self": self.holder,
            "expires_in_s": round(row["expires_at"] - time.time(), 1),
        }


def checkpoint(store: Store, plane, outcome, minute: int) -> None:
    """Write everything needed to resume, as one transaction.

    Called after each tick. If the process dies mid-write, SQLite's WAL leaves
    the previous consistent state intact rather than a half-applied one - which
    matters, because a routing table saved without its diversions would leave
    traffic diverted with nothing watching it.
    """
    store.save_observations([o for o in outcome.observations
                             if o.minute == minute])
    store.save_audit(list(outcome.ledger))
    store.save_routing(plane.routing)
    store.save_diversions(plane.diversions)
    store.set_meta("minute", minute)
    store.prune(minute - store.retain_minutes)
    store.commit()
