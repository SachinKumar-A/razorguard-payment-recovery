"""Attribution must not claim more than the alarms support."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from revenueguard.config import GATEWAYS, ISSUERS, METHODS
from revenueguard.rootcause import attribute

ALL = [f"{g}|{m}|{i}" for g in GATEWAYS for m in METHODS for i in ISSUERS]


def test_gateway_wide_outage_is_attributed_to_the_gateway():
    alarming = [s for s in ALL if s.startswith("gw_beta|")][:12]
    rc = attribute(alarming, ALL)
    assert rc.primary is not None
    assert rc.primary.dimension == "gateway"
    assert rc.primary.value == "gw_beta"


def test_issuer_outage_is_attributed_to_the_issuer():
    alarming = [s for s in ALL if s.endswith("|hdfc")]
    rc = attribute(alarming, ALL)
    assert rc.primary is not None
    assert rc.primary.value == "hdfc"


def test_no_secondary_claim_from_a_handful_of_alarms():
    """Three alarms sharing an issuer is arithmetic, not evidence.

    The narrative must not say 'peers unaffected' when only three slices have
    reported at all.
    """
    alarming = ["gw_gamma|upi|icici", "gw_gamma|card|icici", "gw_gamma|netbanking|icici"]
    rc = attribute(alarming, ALL)
    assert rc.secondary is None or not rc.secondary.confident_secondary
    text = rc.narrate(0.91, 0.70)
    assert "peers" not in text
    assert "Too few slices" in text


def test_secondary_claim_allowed_once_enough_slices_alarm():
    alarming = [f"gw_gamma|upi|{i}" for i in ISSUERS]  # 8 slices, one method
    rc = attribute(alarming, ALL)
    assert rc.primary is not None
    dims = {rc.primary.dimension}
    if rc.secondary is not None:
        dims.add(rc.secondary.dimension)
    assert "method" in dims or "gateway" in dims


def test_scattered_alarms_are_not_over_explained():
    alarming = ["gw_alpha|upi|hdfc", "gw_beta|card|sbi", "gw_gamma|netbanking|rbl"]
    rc = attribute(alarming, ALL)
    assert rc.primary is None or rc.primary.coverage >= 0.5


def test_empty_input_is_unattributed():
    rc = attribute([], ALL)
    assert rc.primary is None
    assert rc.label == "unattributed"
