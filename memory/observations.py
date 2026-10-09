"""Encrypted local event timeline, deliberately excluded from model context.

Observation is evidence, not a learned preference or permission. The normal
memory search cannot accidentally upload this archive to a remote provider.
"""
import json
import time

from security.vault import _dpapi_protect, _dpapi_unprotect


class ObservationStore:
    def __init__(self, db, protect=None, unprotect=None):
        self.db = db
        self.protect = protect or _dpapi_protect
        self.unprotect = unprotect or _dpapi_unprotect

    def append(self, kind, evidence):
        payload = json.dumps(evidence, ensure_ascii=False).encode("utf-8")
        if len(payload) > 16000:
            raise ValueError("Observation exceeds the local event size limit")
        encrypted = self.protect(payload)  # No plaintext fallback.
        self.db.execute("INSERT INTO local_observations(ts,kind,payload) VALUES(?,?,?)",
                        (int(time.time()), str(kind)[:32], encrypted))

    def recent(self, limit=40, query=""):
        limit = max(1, min(200, int(limit)))
        rows = self.db.query("SELECT id,ts,kind,payload FROM local_observations ORDER BY id DESC LIMIT ?",
                             (min(2000, limit * 10) if query else limit,))
        events = []
        for row in rows:
            try:
                evidence = json.loads(self.unprotect(bytes(row["payload"])).decode("utf-8"))
            except (OSError, ValueError):
                continue
            if query and query.casefold() not in json.dumps(evidence, ensure_ascii=False).casefold():
                continue
            events.append({"id": row["id"], "ts": row["ts"], "kind": row["kind"], "evidence": evidence})
            if len(events) >= limit:
                break
        return events

    def trim(self, retention_days=30, max_events=50000):
        self.db.execute("DELETE FROM local_observations WHERE ts<?",
                        (int(time.time()) - int(retention_days) * 86400,))
        self.db.execute("DELETE FROM local_observations WHERE id NOT IN "
                        "(SELECT id FROM local_observations ORDER BY id DESC LIMIT ?)", (int(max_events),))

    def clear(self):
        self.db.execute("DELETE FROM local_observations")

    def status(self):
        row = self.db.query_one("SELECT COUNT(*) AS count, MAX(ts) AS latest FROM local_observations")
        return {"events": row["count"], "latest": row["latest"], "encrypted": True,
                "cloud_upload": False, "model_training": False}
