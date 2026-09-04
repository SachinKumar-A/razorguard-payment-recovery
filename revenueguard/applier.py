"""The last mile: handing a recommendation to something that can act on it.

RevenueGuard cannot change acquirer routing inside Razorpay - that is
Razorpay's own product, not a third-party endpoint, and nothing here pretends
otherwise. What it *can* do is finish its own loop: deliver each recommendation
to a system you nominate, in a form that system can consume, and record what
happened to it.

That is not a technicality. A merchant running more than one payment gateway
chooses which one each transaction goes to in their own checkout. For them these
recommendations are directly actionable, and the only thing standing between the
decision and the action is a piece of glue. This module is that glue, with the
seam left where it belongs.

Three modes, matching the three ways to run this described in DEPLOY.md:

- `off`     - recommendations sit at GET /routing; nothing is pushed.
- `notify`  - every change is delivered, but framed as a proposal for a human.
- `auto`    - changes are delivered as instructions to apply.

`notify` and `auto` send the identical payload. The difference is the `mode`
field and who is expected to be reading. Keeping them the same shape means
moving from one to the other is a config change rather than a rewrite, and that
a team can start at `notify`, watch it for a fortnight, and switch when they
trust it.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Tuple


@dataclass
class Recommendation:
    """One key whose weights should change."""
    method: str
    issuer: str
    weights: Dict[str, float]
    baseline: Dict[str, float]
    minute: int
    reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.method}|{self.issuer}"

    def payload(self, mode: str) -> Dict[str, object]:
        return {
            "source": "revenueguard",
            "mode": mode,
            "minute": self.minute,
            "method": self.method,
            "issuer": self.issuer,
            "weights": {g: round(w, 4) for g, w in self.weights.items()},
            "baseline": {g: round(w, 4) for g, w in self.baseline.items()},
            "reason": self.reason,
        }


@dataclass
class ApplyResult:
    delivered: int = 0
    failed: int = 0
    skipped_unchanged: int = 0
    errors: List[str] = field(default_factory=list)


class RoutingApplier(Protocol):
    mode: str

    def apply(self, recommendations: List[Recommendation]) -> ApplyResult: ...


class NullApplier:
    """Mode `off`. Recommendations are published, never pushed."""

    mode = "off"

    def apply(self, recommendations: List[Recommendation]) -> ApplyResult:
        return ApplyResult(skipped_unchanged=len(recommendations))


class WebhookApplier:
    """POST each changed key to a traffic manager, or to a human's channel.

    Deliberately one request per key rather than one batch. A traffic manager
    that accepts a partial update can act on the keys it understands and reject
    the rest; a batch forces it to take all or nothing, and a single malformed
    key would then block every other recovery in that minute.
    """

    def __init__(self, url: str, mode: str = "notify", timeout: float = 5.0,
                 token: Optional[str] = None):
        self.url = url
        self.mode = mode
        self.timeout = timeout
        self.token = token
        self.delivered = 0
        self.failed = 0
        self.last_error: Optional[str] = None

    def apply(self, recommendations: List[Recommendation]) -> ApplyResult:
        result = ApplyResult()
        for rec in recommendations:
            body = json.dumps(rec.payload(self.mode)).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            request = urllib.request.Request(self.url, data=body,
                                             method="POST", headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as r:
                    if 200 <= r.status < 300:
                        result.delivered += 1
                        self.delivered += 1
                    else:
                        result.failed += 1
                        self.failed += 1
                        self.last_error = f"HTTP {r.status}"
                        result.errors.append(f"{rec.key}: HTTP {r.status}")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                # A failed delivery must not stop the ones behind it, and must
                # never reach the control loop.
                result.failed += 1
                self.failed += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                result.errors.append(f"{rec.key}: {exc}")
        return result


class FileApplier:
    """Write the full routing table to a file the traffic manager watches.

    Common in practice: a config file on a shared volume, reloaded on change.
    The write is atomic - a temporary file then a rename - because a reader that
    catches a half-written table would apply nonsense weights, and this is the
    one place where a partial read moves real money.
    """

    mode = "auto"

    def __init__(self, path: str):
        self.path = path
        self.writes = 0
        self.failed = 0
        self.last_error: Optional[str] = None

    def apply(self, recommendations: List[Recommendation]) -> ApplyResult:
        result = ApplyResult()
        if not recommendations:
            return result

        document = {
            "source": "revenueguard",
            "written_at": time.time(),
            "minute": recommendations[0].minute,
            "keys": {r.key: {g: round(w, 4) for g, w in r.weights.items()}
                     for r in recommendations},
        }
        tmp = f"{self.path}.tmp"
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(document, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            self.writes += 1
            result.delivered = len(recommendations)
        except OSError as exc:
            self.failed += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            result.failed = len(recommendations)
            result.errors.append(str(exc))
        return result


class ChangeTracker:
    """Only push what actually changed.

    The routing table is re-read every tick, but most ticks change nothing. A
    traffic manager receiving an identical instruction sixty times an hour would
    reasonably start ignoring them, so recommendations are compared against what
    was last delivered and unchanged keys are skipped.
    """

    def __init__(self, epsilon: float = 0.002):
        self.epsilon = epsilon
        self._last: Dict[str, Dict[str, float]] = {}

    def changed(self, recommendations: List[Recommendation]
                ) -> Tuple[List[Recommendation], int]:
        out, skipped = [], 0
        for rec in recommendations:
            previous = self._last.get(rec.key)
            if previous is not None and self._same(previous, rec.weights):
                skipped += 1
                continue
            self._last[rec.key] = dict(rec.weights)
            out.append(rec)
        return out, skipped

    def _same(self, a: Dict[str, float], b: Dict[str, float]) -> bool:
        if a.keys() != b.keys():
            return False
        return all(abs(a[g] - b[g]) <= self.epsilon for g in a)

    def forget(self, key: str) -> None:
        """A key back at baseline should be pushed again if it diverges later."""
        self._last.pop(key, None)


def recommendations_from(routing, minute: int,
                         reason: str = "") -> List[Recommendation]:
    """Every key currently away from baseline."""
    return [
        Recommendation(method=m, issuer=i,
                       weights=dict(routing.current[(m, i)]),
                       baseline=dict(routing.baseline[(m, i)]),
                       minute=minute, reason=reason)
        for (m, i) in routing.current if routing.is_diverted(m, i)
    ]


def build_from_env() -> RoutingApplier:
    """Configure from the environment. Defaults to publishing only."""
    mode = os.environ.get("REVENUEGUARD_APPLY_MODE", "off").strip().lower()
    if mode == "off":
        return NullApplier()

    path = os.environ.get("REVENUEGUARD_APPLY_FILE", "").strip()
    if path:
        return FileApplier(path)

    url = os.environ.get("REVENUEGUARD_APPLY_WEBHOOK", "").strip()
    if url:
        return WebhookApplier(
            url, mode=mode if mode in ("notify", "auto") else "notify",
            token=os.environ.get("REVENUEGUARD_APPLY_TOKEN") or None)

    raise RuntimeError(
        f"REVENUEGUARD_APPLY_MODE={mode} needs somewhere to send to. Set "
        f"REVENUEGUARD_APPLY_WEBHOOK or REVENUEGUARD_APPLY_FILE, or leave the "
        f"mode as 'off' to publish at GET /routing only.")
