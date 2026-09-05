"""The console's data layer: small payload, honest cache.

Two things are worth a test here. The payload must stay small, because it
carrying the raw observations is what made the console take three-quarters of a
minute to open every time. And the cache must be a cache: a second call has to
return the same numbers without running the control plane again.
"""
from __future__ import annotations

import pathlib
import pickle
import time

import pytest

from razorguard import console_data


@pytest.fixture()
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(console_data, "CACHE", tmp_path / "cache")
    return tmp_path / "cache"


def test_payload_carries_no_observations(cache_dir):
    """The fee total, not two hundred thousand dataclasses."""
    data = console_data.run(1, 3, routing=True)
    assert "observations" not in data
    assert isinstance(data["processing_cost"], float)
    # A payload this size can live on disk. One with the observations in it
    # was two orders of magnitude larger, which is why it did not.
    assert len(pickle.dumps(data)) < 2_000_000


def test_second_call_comes_from_disk_and_agrees(cache_dir):
    t0 = time.perf_counter()
    first = console_data.run(1, 3, routing=True)
    cold = time.perf_counter() - t0

    assert list(cache_dir.glob("run-1-3-1-*.pkl")), "nothing was written"

    t0 = time.perf_counter()
    second = console_data.run(1, 3, routing=True)
    warm = time.perf_counter() - t0

    for key in ("attempts", "successes", "revenue", "actions", "blocked",
                "processing_cost", "exposure"):
        assert first[key] == second[key], key
    assert warm < cold / 4, f"cache bought nothing: {cold:.2f}s then {warm:.2f}s"


def test_a_corrupt_cache_file_is_ignored_not_fatal(cache_dir):
    """A half-written pickle should cost speed, never correctness."""
    good = console_data.run(1, 3, routing=False)
    f = next(iter(cache_dir.glob("run-1-3-0-*.pkl")))
    f.write_bytes(b"not a pickle")
    again = console_data.run(1, 3, routing=False)
    assert again["successes"] == good["successes"]


def test_per_incident_stats_stay_inside_the_run(cache_dir):
    """Every incident is a subset of the run, and the source set is a subset
    of the cohort - never the other way round."""
    data = console_data.run(1, 3, routing=False)
    rows = data["incidents"]
    assert not rows.empty
    for row in rows.itertuples():
        assert row.coh_att <= data["attempts"]
        assert row.src_att <= row.coh_att
        assert row.src_suc <= row.src_att
        assert row.coh_suc <= row.coh_att
        # The lookback window is the hour before the incident, on the same
        # routes - so a run whose first incident starts at minute 0 is the
        # only case with no baseline to measure against.
        if row.start >= 60:
            assert row.src_pre_att > 0, row.id
            assert row.coh_pre_att > 0, row.id


# ---------------------------------------------------------------------------
# The cohort. This is a regression test for a bug the console shipped with for
# exactly one render: per-incident recovery was measured over the slices the
# incident broke, and the biggest, best-handled outage therefore reported a
# loss of 19 lakh. Succeeding at routing means emptying the broken slices - the
# traffic, and the money with it, moves to a different gateway. Measuring the
# source alone measures only the half that leaves.
# ---------------------------------------------------------------------------

def _arms(days=1, seed=3):
    c = console_data.run(days, seed, routing=False)
    t = console_data.run(days, seed, routing=True)
    return (c, t, c["incidents"].set_index("id"), t["incidents"].set_index("id"))


def test_the_cohort_sees_identical_demand_in_both_arms(cache_dir):
    """Demand is drawn per (minute, method, issuer), so a cohort defined on
    those pairs is invariant to routing. If this ever fails, no per-incident
    recovery figure means anything."""
    _, _, ci, ti = _arms()
    for k in ti.index:
        assert ci.loc[k]["coh_att"] == ti.loc[k]["coh_att"], k


def test_the_source_set_alone_does_not_see_identical_demand(cache_dir):
    """The other half of the same fact, and the reason the source set cannot
    be used for recovery: routing deliberately drains it."""
    _, _, ci, ti = _arms()
    drained = [k for k in ti.index if ti.loc[k]["src_att"] < ci.loc[k]["src_att"]]
    assert drained, ("no incident had traffic routed away from it, so this "
                     "run cannot demonstrate the distinction")


def test_routable_outages_recover_money_on_the_cohort(cache_dir):
    """A gateway outage has healthy destinations by construction. If the
    cohort measurement is right, those are the incidents that show a gain."""
    _, _, ci, ti = _arms()
    outages = [k for k in ti.index if ti.loc[k]["kind"] == "hard_outage"]
    assert outages
    for k in outages:
        assert ti.loc[k]["coh_rev"] > ci.loc[k]["coh_rev"], k


def test_per_incident_recovery_is_close_to_the_run_total(cache_dir):
    """Cohorts overlap when incidents overlap in time, so the sum runs a
    little high. A little. If it drifts far from the whole-run figure, the
    windows or the cohorts are wrong."""
    c, t, ci, ti = _arms()
    per_incident = sum(ti.loc[k]["coh_rev"] - ci.loc[k]["coh_rev"] for k in ti.index)
    whole_run = t["revenue"] - c["revenue"]
    assert whole_run > 0
    assert abs(per_incident - whole_run) / whole_run < 0.25


# ---------------------------------------------------------------------------
# Tunable settings. The console's settings page feeds these straight into the
# simulator and the policy engine, so a control that quietly does nothing is
# worse than no control at all.
# ---------------------------------------------------------------------------

def test_defaults_are_the_same_run_as_no_tuning(cache_dir):
    """Passing the defaults explicitly must not be a different run, or every
    link the console builds would miss the cache it just filled."""
    assert (console_data.cache_key(1, 3, True)
            == console_data.cache_key(1, 3, True, console_data.defaults()))
    a = console_data.run(1, 3, routing=True)
    b = console_data.run(1, 3, routing=True, tuning=console_data.defaults())
    assert a["successes"] == b["successes"]
    assert a["actions"] == b["actions"]


def test_a_tighter_shift_cap_recovers_less(cache_dir):
    """The measurement that set the default: 80% against the a-priori 40%."""
    wide_c = console_data.run(1, 3, False)
    wide_t = console_data.run(1, 3, True)
    tight = {"max_shift": 0.40}
    tight_c = console_data.run(1, 3, False, tight)
    tight_t = console_data.run(1, 3, True, tight)

    wide = wide_t["revenue"] - wide_c["revenue"]
    narrow = tight_t["revenue"] - tight_c["revenue"]
    assert wide > 0
    assert narrow < wide, "the shift cap is not reaching the policy engine"


def test_traffic_volume_reaches_the_simulator(cache_dir):
    thin = console_data.run(1, 3, True, {"txn_per_min": 200.0})
    assert thin["attempts"] < console_data.run(1, 3, True)["attempts"]


def test_every_tunable_changes_the_cache_key(cache_dir):
    """A knob that does not change the key would silently serve another
    setting's cached run - the worst possible failure, because it looks like
    the setting had no effect."""
    base = console_data.cache_key(2, 7, True)
    for name, value in console_data.defaults().items():
        moved = console_data.cache_key(2, 7, True, {name: float(value) + 1})
        assert moved != base, name


def test_the_refusal_decomposition_does_not_go_negative(cache_dir):
    """The console explains the engine's refusal count as rule-named refusals,
    plus rollbacks, plus alarms a single breaker entry covered. If the first
    two ever exceeded the total, that third line would be a negative number
    presented as an explanation."""
    t = console_data.run(1, 3, routing=True)
    total = t["blocked"] + t["escalated"]
    by_rule = sum(t["blocked_by_rule"].values())
    assert by_rule + t["rollbacks"] <= total


# ---------------------------------------------------------------------------
# The console itself. These run the real Streamlit script through its test
# harness, which is the only way to catch the class of break that does not
# raise until a particular page is rendered.
# ---------------------------------------------------------------------------

VIEWS = ["overview", "decisions", "replay", "recovery", "actions", "refused",
         "rollbacks", "audit", "method", "settings"]


def _app(**params):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(
        str(pathlib.Path(__file__).resolve().parent.parent / "app.py"),
        default_timeout=900)
    for k, v in params.items():
        at.query_params[k] = str(v)
    return at.run()


@pytest.mark.parametrize("view", VIEWS)
def test_every_page_renders(view):
    at = _app(view=view, days=1, seed=3)
    assert not at.exception, [str(e.value) for e in at.exception]


def test_navigation_does_not_leave_the_page():
    """The navigation is buttons rather than anchors, so a click reruns the
    script and rewrites the URL instead of loading the document again. If these
    ever go back to being links, the click below stops changing anything."""
    at = _app(days=1, seed=3)
    keys = {b.key for b in at.button}
    assert "nav_refused" in keys and "kpi_Rollbacks" in keys

    # The harness hands query parameters back as lists; the runtime hands them
    # back as strings. Normalise rather than assert on the harness's shape.
    def view_of(t):
        v = t.query_params["view"]
        return v[0] if isinstance(v, list) else v

    at.button(key="nav_refused").click().run()
    assert view_of(at) == "refused"
    assert not at.exception

    at.button(key="kpi_Rollbacks").click().run()
    assert view_of(at) == "rollbacks"
    assert not at.exception


def test_settings_is_off_the_navigation_bar():
    at = _app(days=1, seed=3)
    assert not any(b.key == "nav_settings" for b in at.button)


def test_a_clicked_minute_opens_with_that_minute_s_ledger():
    """The chart's click selection cannot be driven from the test harness, so
    the same panel is reachable by `?at=`, which is also how a particular
    minute gets shared as a link."""
    at = _app(view="overview", days=1, seed=3, at=200)
    assert not at.exception
    panel = [m.value for m in at.markdown if 'class="moment"' in m.value]
    assert panel, "clicking a minute produced no panel"
    assert "d1 03:20" in panel[0]
