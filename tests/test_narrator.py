"""The narration layer must never be able to affect a decision, and never crash.

Both properties are load-bearing. The first is the reason an LLM is allowed
anywhere near this system; the second is what makes it safe to leave enabled.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from revenueguard.config import GATEWAYS, ISSUERS, METHODS
from revenueguard.narrator import (ClaudeNarrator, TemplateNarrator, _facts,
                                   build, describe)
from revenueguard.rootcause import attribute

ALL = [f"{g}|{m}|{i}" for g in GATEWAYS for m in METHODS for i in ISSUERS]


def _cause(n=10):
    return attribute([s for s in ALL if s.startswith("gw_beta|")][:n], ALL)


def test_template_narrator_never_needs_anything():
    text = TemplateNarrator().narrate(_cause(), 0.95, 0.60)
    assert "gw_beta" in text
    assert len(text) > 40


def test_build_defaults_to_template():
    assert isinstance(build(), TemplateNarrator)
    assert isinstance(build("nonsense-backend"), TemplateNarrator)


def test_claude_narrator_construction_never_raises():
    """Safe to leave `--claude` in a script on a machine with no key."""
    n = ClaudeNarrator()
    assert isinstance(describe(n), str)


def test_claude_narrator_falls_back_when_unavailable():
    n = ClaudeNarrator()
    n.client = None
    n.disabled_reason = "no credential"
    text = n.narrate(_cause(), 0.95, 0.60)
    assert text == TemplateNarrator().narrate(_cause(), 0.95, 0.60)
    assert n.fallbacks == 1


def test_any_client_exception_falls_back_rather_than_propagating():
    """Including exceptions the SDK does not type.

    With no credential the SDK raises a bare TypeError from header
    construction, which an `except anthropic.APIStatusError` chain misses.
    """
    class Boom:
        class messages:
            @staticmethod
            def create(**_):
                raise TypeError("Could not resolve authentication method")

    n = ClaudeNarrator()
    n.client = Boom()
    text = n.narrate(_cause(), 0.95, 0.60)
    assert "gw_beta" in text
    assert n.fallbacks == 1
    assert "TypeError" in (n.disabled_reason or "")


def test_narrator_stops_calling_out_after_the_first_failure():
    """A missing key must not cost one failed round trip per alarming slice."""
    calls = {"n": 0}

    class Counting:
        class messages:
            @staticmethod
            def create(**_):
                calls["n"] += 1
                raise RuntimeError("network down")

    n = ClaudeNarrator()
    n.client = Counting()
    for _ in range(25):
        n.narrate(_cause(), 0.95, 0.60)
    assert calls["n"] == 1
    assert n.fallbacks == 25


def test_refusal_falls_back():
    class Refusing:
        class messages:
            @staticmethod
            def create(**_):
                class R:
                    stop_reason = "refusal"
                    content = []
                return R()

    n = ClaudeNarrator()
    n.client = Refusing()
    assert "gw_beta" in n.narrate(_cause(), 0.95, 0.60)
    assert n.fallbacks == 1


def test_empty_completion_falls_back():
    class Empty:
        class messages:
            @staticmethod
            def create(**_):
                class B:
                    type = "text"
                    text = "   "
                class R:
                    stop_reason = "end_turn"
                    content = [B()]
                return R()

    n = ClaudeNarrator()
    n.client = Empty()
    assert "gw_beta" in n.narrate(_cause(), 0.95, 0.60)
    assert n.fallbacks == 1


def test_model_sees_only_computed_facts_never_raw_traffic():
    """The prompt must carry the attribution's conclusions, not the evidence.

    If slice keys or per-minute counts ever reach the prompt, the model is
    being asked to do inference rather than transcription.
    """
    cause = _cause()
    prompt = _facts(cause, 0.95, 0.60).as_prompt()
    assert "gw_beta" in prompt                      # the conclusion
    assert "attempts" not in prompt.lower()         # not the raw evidence
    assert "|" not in prompt                        # no slice keys
    assert "minute" not in prompt.lower()


def test_narrator_module_cannot_reach_the_decision_path():
    """Structural: nothing in the decision path imports the narrator."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "revenueguard"
    for name in ("policy.py", "routing.py", "control_plane.py", "audit.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "narrator" not in src, f"{name} must not import the narrator"
