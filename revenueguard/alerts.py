"""Getting escalations to a human.

Until now every escalation ended in the audit ledger, which is the right place
to *record* it and the wrong place to leave it. A system that decides a human
should look, and then tells nobody, has not escalated - it has filed.

Design constraints, in order of importance:

1. **Never block the control loop.** Alert delivery happens on its own thread
   with a bounded queue. A slow or dead webhook must not delay detection, and
   if the queue fills, alerts are dropped and counted rather than applying
   backpressure to the thing that matters.
2. **Never crash it either.** Every failure is caught and counted. A misspelled
   URL should degrade alerting, not the router.
3. **Do not flood.** One outage produces many escalations across its slices;
   identical alerts inside a cooldown are suppressed, with the suppression
   count carried on the next one that gets through so nothing is silently lost.

Delivery is a plain HTTP POST, which every incident tool accepts either
directly or through a bridge.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, Optional

#: Identical alerts inside this window are folded into a count.
DEDUPE_SECONDS = 300
#: Bounded on purpose - see constraint 1.
QUEUE_DEPTH = 256


@dataclass
class Alert:
    minute: int
    kind: str          # escalation | rollback
    subject: str
    summary: str
    rule: Optional[str] = None
    extra: Dict[str, object] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return f"{self.kind}|{self.subject}|{self.rule or ''}"

    def payload(self, suppressed: int = 0) -> Dict[str, object]:
        body = {
            "source": "revenueguard",
            "minute": self.minute,
            "kind": self.kind,
            "subject": self.subject,
            "summary": self.summary,
            "rule": self.rule,
        }
        body.update(self.extra)
        if suppressed:
            body["suppressed_duplicates"] = suppressed
        return body


class Alerter:
    """Best-effort webhook delivery on a background thread."""

    def __init__(self, url: Optional[str] = None, timeout: float = 5.0,
                 dedupe_seconds: int = DEDUPE_SECONDS):
        self.url = url or os.environ.get("REVENUEGUARD_ALERT_WEBHOOK") or None
        self.timeout = timeout
        self.dedupe_seconds = dedupe_seconds

        self.sent = 0
        self.failed = 0
        self.dropped_full = 0
        self.suppressed = 0
        self.last_error: Optional[str] = None

        self._queue: "queue.Queue[Alert]" = queue.Queue(maxsize=QUEUE_DEPTH)
        self._last_sent: Dict[str, float] = {}
        self._pending_dupes: Dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if self.url:
            self._thread = threading.Thread(target=self._drain, daemon=True,
                                            name="revenueguard-alerts")
            self._thread.start()

    @property
    def enabled(self) -> bool:
        return self.url is not None

    def send(self, alert: Alert) -> bool:
        """Queue an alert. Returns False if it was dropped or suppressed."""
        if not self.enabled:
            return False

        now = time.time()
        key = alert.dedupe_key
        last = self._last_sent.get(key)
        if last is not None and now - last < self.dedupe_seconds:
            self._pending_dupes[key] = self._pending_dupes.get(key, 0) + 1
            self.suppressed += 1
            return False

        try:
            self._queue.put_nowait(alert)
        except queue.Full:
            # Dropping is the correct failure here: the alternative is blocking
            # the control loop behind an incident channel.
            self.dropped_full += 1
            return False

        self._last_sent[key] = now
        return True

    def _drain(self) -> None:  # pragma: no cover - background thread
        while not self._stop.is_set():
            try:
                alert = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._post(alert)

    def _post(self, alert: Alert) -> None:
        key = alert.dedupe_key
        suppressed = self._pending_dupes.pop(key, 0)
        data = json.dumps(alert.payload(suppressed)).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=data, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                if 200 <= resp.status < 300:
                    self.sent += 1
                else:
                    self.failed += 1
                    self.last_error = f"HTTP {resp.status}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.failed += 1
            self.last_error = f"{type(exc).__name__}: {exc}"

    def stats(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled,
            "sent": self.sent,
            "failed": self.failed,
            "suppressed": self.suppressed,
            "dropped_queue_full": self.dropped_full,
            "queued": self._queue.qsize(),
            "last_error": self.last_error,
        }

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def alerts_from_ledger(events, minute: int) -> list:
    """Pick the events a human actually needs to see from one tick.

    Escalations and rollbacks only. Detections, proposals and allowed actions
    are the system working; paging on those is how an alert channel becomes
    something people mute.
    """
    out = []
    for e in events:
        if e.minute != minute:
            continue
        if e.kind == "rollback":
            out.append(Alert(minute=e.minute, kind="rollback",
                             subject=e.subject, summary=e.summary,
                             rule=e.rule, extra={"seq": e.seq}))
        elif e.kind == "decision" and e.evidence.get("decision") == "escalate":
            out.append(Alert(minute=e.minute, kind="escalation",
                             subject=e.subject, summary=e.summary,
                             rule=e.rule, extra={"seq": e.seq}))
    return out
