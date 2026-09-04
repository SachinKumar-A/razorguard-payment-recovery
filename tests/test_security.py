"""`/ingest` steers money decisions, so it does not get to be open by accident."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from revenueguard.security import (CLOCK_SKEW_SECONDS, AuthConfig, AuthError,
                                   sign, verify)

BODY = b'{"outcomes":[]}'


def ok(config, **kw):
    """verify() raises on failure and returns None on success."""
    args = {"authorization": None, "timestamp": None, "signature": None,
            "body": BODY}
    args.update(kw)
    verify(config, **args)


# -- configuration -----------------------------------------------------------

def test_missing_configuration_refuses_to_start(monkeypatch):
    """Open must be the thing you opt into, not the thing you forget."""
    for var in ("REVENUEGUARD_TOKEN", "REVENUEGUARD_HMAC_KEY",
                "REVENUEGUARD_ALLOW_INSECURE"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="no credential configured"):
        AuthConfig.from_env()


def test_insecure_requires_an_explicit_opt_in(monkeypatch):
    for var in ("REVENUEGUARD_TOKEN", "REVENUEGUARD_HMAC_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("REVENUEGUARD_ALLOW_INSECURE", "1")
    config = AuthConfig.from_env()
    assert config.mode == "insecure"
    assert config.enforced is False


def test_hmac_is_preferred_when_both_are_set(monkeypatch):
    monkeypatch.setenv("REVENUEGUARD_TOKEN", "t")
    monkeypatch.setenv("REVENUEGUARD_HMAC_KEY", "k")
    assert AuthConfig.from_env().mode == "hmac"


# -- token mode --------------------------------------------------------------

def test_token_accepts_the_right_one():
    ok(AuthConfig("token", "s3cret"), authorization="Bearer s3cret")


def test_token_rejects_missing_wrong_and_malformed():
    config = AuthConfig("token", "s3cret")
    for header, reason in ((None, "missing Authorization"),
                           ("Bearer wrong", "invalid token"),
                           ("s3cret", "expected 'Authorization"),
                           ("Basic s3cret", "expected 'Authorization"),
                           ("Bearer ", "expected 'Authorization")):
        with pytest.raises(AuthError, match=reason):
            ok(config, authorization=header)


# -- hmac mode ---------------------------------------------------------------

def test_hmac_accepts_a_correct_signature():
    config = AuthConfig("hmac", "key")
    ts = str(int(time.time()))
    ok(config, timestamp=ts, signature=sign("key", ts, BODY))


def test_hmac_rejects_a_tampered_body():
    config = AuthConfig("hmac", "key")
    ts = str(int(time.time()))
    signature = sign("key", ts, BODY)
    with pytest.raises(AuthError, match="signature does not match"):
        verify(config, None, ts, signature, b'{"outcomes":[{"attempts":1}]}')


def test_hmac_rejects_a_replayed_request():
    config = AuthConfig("hmac", "key")
    stale = str(int(time.time()) - CLOCK_SKEW_SECONDS - 60)
    with pytest.raises(AuthError, match="outside the"):
        ok(config, timestamp=stale, signature=sign("key", stale, BODY))


def test_hmac_rejects_a_future_timestamp():
    """A far-future stamp is as suspicious as a stale one."""
    config = AuthConfig("hmac", "key")
    ahead = str(int(time.time()) + CLOCK_SKEW_SECONDS + 60)
    with pytest.raises(AuthError, match="outside the"):
        ok(config, timestamp=ahead, signature=sign("key", ahead, BODY))


def test_hmac_rejects_a_wrong_key():
    config = AuthConfig("hmac", "key")
    ts = str(int(time.time()))
    with pytest.raises(AuthError, match="signature does not match"):
        ok(config, timestamp=ts, signature=sign("other", ts, BODY))


def test_hmac_rejects_missing_headers_and_junk_timestamps():
    config = AuthConfig("hmac", "key")
    with pytest.raises(AuthError, match="missing X-RevenueGuard"):
        ok(config, timestamp=None, signature="abc")
    with pytest.raises(AuthError, match="not a unix time"):
        ok(config, timestamp="yesterday", signature="abc")


def test_signature_binds_the_timestamp_to_the_body():
    """Otherwise a valid signature could be replayed under a new timestamp."""
    a = sign("key", "1000", BODY)
    b = sign("key", "2000", BODY)
    assert a != b


# -- insecure ----------------------------------------------------------------

def test_insecure_mode_lets_everything_through():
    ok(AuthConfig("insecure"))
