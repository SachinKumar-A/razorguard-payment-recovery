"""Every headline number in the documentation must match the benchmark output.

The PDFs interpolate their figures from `bench/results/*.json`, so they cannot
drift. The README and SUBMIT.md do not - their numbers are written into prose
and tables, which is the normal way documentation quietly becomes false. A claim
that "no number here was typed by hand" is worth nothing unless something checks
it.

This is that check. Each entry pairs a phrase from a document with the JSON
field it is supposed to be reporting. If a benchmark is re-run and a document is
not updated, these fail and say exactly which claim went stale.

Indian digit grouping (1,22,91,379) is handled explicitly, because that is how
the documents are written and a naive `f"{n:,}"` would never match.
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "bench", "results")


def load(name):
    with open(os.path.join(RESULTS, f"{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def text(name):
    return io.open(os.path.join(ROOT, name), encoding="utf-8").read()


def indian(value: float) -> str:
    """1234567.8 -> '12,34,568'. The grouping the documents actually use."""
    digits = str(int(round(value)))
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
    return f"{head},{tail}"


def plain(value: float) -> str:
    return f"{int(round(value)):,}"


@pytest.fixture(scope="module")
def results():
    return {"validation": load("validation"), "experiment": load("experiment"),
            "stress": load("stress"), "sensitivity": load("sensitivity"),
            "sweep": load("sweep")}


def _claims(results):
    val = results["validation"]["summary"]
    exp = results["experiment"]
    rec = exp["recovered"]
    stress = results["stress"]
    shipped = stress["shipped"]

    return [
        # (document, what it claims, the string that must appear)
        ("README.md", "validated mean recovery",
         indian(val["recovered_inr"]["mean"])),
        ("README.md", "confidence interval lower bound",
         indian(val["recovered_inr"]["ci_lo"])),
        ("README.md", "confidence interval upper bound",
         indian(val["recovered_inr"]["ci_hi"])),
        ("README.md", "share of exposure",
         f"{val['share_of_exposure']['mean']:.1%}"),
        ("README.md", "seeds that lost money",
         f"{results['validation']['seeds_with_loss']}"),
        ("README.md", "total attempts per arm", plain(exp["attempts"])),
        ("README.md", "control successes",
         plain(exp["control"]["successes"])),
        ("README.md", "treatment successes",
         plain(exp["treatment"]["successes"])),
        ("README.md", "payments saved on the detailed seed",
         plain(rec["payments"])),
        ("README.md", "shipped shift cap share of exposure",
         f"{shipped['share_of_exposure']:.1%}"),
        ("README.md", "net recovery after processing fees",
         indian(exp["net"]["net_inr"])),
        ("README.md", "incremental processing fees",
         indian(exp["net"]["incremental_cost_inr"])),
        ("SUBMIT.md", "validated mean recovery in the form text",
         indian(val["recovered_inr"]["mean"])),
    ] + _detector_claims(results)


def _detector_claims(results):
    """The matched-false-alarm table, which is easy to leave stale."""
    matched = results["sweep"].get("matched") or {}
    out = []
    for family in ("fixed", "posterior", "union"):
        row = matched.get(family)
        if not row:
            continue
        out.append(("README.md", f"{family} detection count",
                    f"{row['detected']} / {row['incidents']}"))
        out.append(("README.md", f"{family} median time to detect",
                    f"{row['median_ttd']:g} min"))
    return out


def test_every_documented_number_matches_the_benchmarks(results):
    stale = []
    for document, claim, needle in _claims(results):
        if needle not in text(document):
            stale.append(f"{document}: {claim} -> expected to find "
                         f"'{needle}', which the benchmark output says")
    assert not stale, (
        "documentation has drifted from bench/results:\n  "
        + "\n  ".join(stale)
        + "\n\nRe-run the benchmark and update the document, or revert the "
          "benchmark. A number in prose that no command produces is the "
          "thing this repository exists to avoid.")


def test_the_sensitivity_finding_is_still_reported(results):
    """The most important limitation must not quietly fall out of the README."""
    harmful = results["sensitivity"]["harmful_curves"]
    readme = text("README.md")
    if harmful:
        assert any(name in readme for name in harmful), (
            f"the sweep found curves where routing loses money ({harmful}) "
            f"and the README does not mention them")
        assert "efficacy" in readme.lower() or "breaker" in readme.lower(), (
            "the README reports harmful curves but not the mechanism that "
            "limits the damage")


def test_no_result_file_is_empty_or_stale_shaped(results):
    assert results["validation"]["summary"]["recovered_inr"]["n"] >= 2
    assert results["experiment"]["valid"] is True
    assert len(results["stress"]["settings"]) >= 3
    assert len(results["sensitivity"]["cells"]) >= 8


def test_the_readme_does_not_claim_a_figure_the_json_contradicts(results):
    """Guard against the specific failure of editing one and not the other."""
    readme = text("README.md")
    old_headlines = ["1,23,56,984", "8,562,895", "85,62,895", "74,45,470"]
    present = [h for h in old_headlines if h in readme]
    assert not present, (
        f"README still quotes superseded headline figures: {present}")


# ---------------------------------------------------------------------------
# The pitch script. It is spoken aloud on camera, so a stale figure in it is
# worse than a stale figure in a document nobody reads out.
# ---------------------------------------------------------------------------

def test_the_pitch_script_quotes_the_benchmarks(results):
    """Every number said on camera has to be one the repository produces."""
    from docs.build_docs import Facts, rupees
    from docs.pitch import script

    spoken = []

    import docs.pitch as pitch
    original = pitch.beat

    def capture(clock, seconds, title, screen, words, note=""):
        spoken.append(words)
        return original(clock, seconds, title, screen, words, note)

    pitch.beat = capture
    try:
        script(Facts())
    finally:
        pitch.beat = original

    joined = " ".join(spoken)
    val = results["validation"]["summary"]
    for figure in (rupees(val["recovered_inr"]["mean"]),
                   rupees(val["recovered_inr"]["ci_lo"]),
                   rupees(val["recovered_inr"]["ci_hi"]),
                   f"{val['share_of_exposure']['mean']:.1%}",
                   f"{val['sr_gain_pp']['mean']:.2f}"):
        assert figure in joined, figure


def test_the_pitch_fits_three_minutes():
    """413 words is about 165 seconds at a normal speaking pace. Past roughly
    450 there is no room left for the pauses and the clicking, and the take
    runs over."""
    import re

    from docs.build_docs import Facts
    import docs.pitch as pitch

    spoken = []
    original = pitch.beat

    def capture(clock, seconds, title, screen, words, note=""):
        spoken.append(words)
        return original(clock, seconds, title, screen, words, note)

    pitch.beat = capture
    try:
        pitch.script(Facts())
    finally:
        pitch.beat = original

    words = 0
    for block in spoken:
        plain = re.sub(r"\[[^\]]*\]", "", re.sub(r"<[^>]+>", "", block))
        plain = plain.replace("&mdash;", " ").replace("&rarr;", " ")
        words += len(plain.split())
    assert 350 <= words <= 450, f"{words} words is not a three-minute script"
