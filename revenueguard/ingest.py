"""The seam where real payment outcomes replace the simulator.

Everything upstream of this module is evaluation machinery: a simulator, an
incident plan, a control arm. Everything downstream - detection, attribution,
policy, routing, audit - never knew any of that existed. It consumes a stream of

    (slice, minute, attempts, successes)

and nothing else. That was a deliberate constraint from the first commit, and
this module is what it buys: to run against production traffic you replace the
source and change nothing else.

Three sources ship here:

- `SimulatedSource` wraps the existing `World`, so the benchmarks keep working.
- `BufferedSource` accepts observations pushed in from outside - a webhook, a
  Kafka consumer, a query against your payments table - and hands them to the
  loop one minute at a time.
- `CsvSource` reads a file with the same four columns, which is the fastest way
  to replay a real historical incident through the system.

What a deployment still has to decide
-------------------------------------
The control plane emits routing *recommendations*. It cannot apply them,
because acquirer selection is not an endpoint a third party can call (see
`executor.py`). A deployment has to route those recommendations somewhere that
can act on them - an internal traffic manager, a config service, or a human.
That boundary is real and is not hidden here.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Protocol

from .simulator import Observation


class ObservationSource(Protocol):
    """Where a minute's worth of payment outcomes comes from."""

    def poll(self, minute: int) -> List[Observation]:
        """Return every observation for `minute`. May be empty."""
        ...


class SimulatedSource:
    """The benchmark world. Routing feeds back into demand."""

    def __init__(self, world, routing) -> None:
        self.world = world
        self.routing = routing

    def poll(self, minute: int) -> List[Observation]:
        return self.world.step(minute, self.routing)


@dataclass
class PaymentOutcome:
    """One aggregated bucket, as a caller would post it.

    Aggregated rather than per-transaction on purpose: the detector only ever
    needs counts, so a deployment never has to ship individual payment records
    into this system. That keeps the blast radius of an integration small and
    the data it holds uninteresting.
    """
    gateway: str
    method: str
    issuer: str
    attempts: int
    successes: int
    minute: Optional[int] = None

    def validate(self) -> None:
        if self.attempts < 0 or self.successes < 0:
            raise ValueError("attempts and successes must be non-negative")
        if self.successes > self.attempts:
            raise ValueError(
                f"successes ({self.successes}) exceeds attempts "
                f"({self.attempts}) for {self.gateway}|{self.method}|{self.issuer}")

    def to_observation(self, minute: int, ticket: float) -> Observation:
        return Observation(
            minute=minute,
            slice_key=f"{self.gateway}|{self.method}|{self.issuer}",
            method=self.method,
            attempts=self.attempts,
            successes=self.successes,
            avg_ticket_inr=ticket,
        )


class BufferedSource:
    """Accepts outcomes pushed from outside, releases them by minute.

    Late arrivals are the normal case in a real pipeline, so an outcome landing
    for a minute the loop has already processed is counted and dropped rather
    than silently folded into the current minute - which would corrupt the
    baseline the detector compares against. `dropped_late` is exposed so a
    deployment can alert on it instead of discovering it in a postmortem.
    """

    def __init__(self, ticket_for: Callable[[str], float]) -> None:
        self._pending: Dict[int, List[Observation]] = defaultdict(list)
        self._ticket_for = ticket_for
        self._last_released = -1
        self.dropped_late = 0
        self.accepted = 0

    def push(self, outcome: PaymentOutcome, minute: int) -> bool:
        outcome.validate()
        if minute <= self._last_released:
            self.dropped_late += 1
            return False
        self._pending[minute].append(
            outcome.to_observation(minute, self._ticket_for(outcome.method)))
        self.accepted += 1
        return True

    def push_many(self, outcomes: Iterable[PaymentOutcome], minute: int) -> int:
        return sum(1 for o in outcomes if self.push(o, minute))

    def poll(self, minute: int) -> List[Observation]:
        self._last_released = minute
        return self._pending.pop(minute, [])

    @property
    def buffered_minutes(self) -> int:
        return len(self._pending)


class CsvSource:
    """Replay a historical incident from a file.

    Expects a header with: minute, gateway, method, issuer, attempts, successes.
    """

    def __init__(self, path: str, ticket_for: Callable[[str], float]) -> None:
        self._by_minute: Dict[int, List[Observation]] = defaultdict(list)
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                outcome = PaymentOutcome(
                    gateway=row["gateway"].strip(),
                    method=row["method"].strip(),
                    issuer=row["issuer"].strip(),
                    attempts=int(row["attempts"]),
                    successes=int(row["successes"]),
                )
                outcome.validate()
                minute = int(row["minute"])
                self._by_minute[minute].append(
                    outcome.to_observation(minute,
                                           ticket_for(outcome.method)))
        self.minutes = sorted(self._by_minute)

    def poll(self, minute: int) -> List[Observation]:
        return self._by_minute.get(minute, [])

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_minute.values())
