import pytest

from watchdogdatamodel.fingerprint import compute_fingerprint


def test_series_plus_check():
    assert compute_fingerprint("se-se1-production-entsoe", "freshness") == (
        "se-se1-production-entsoe|freshness"
    )


def test_discriminator_appends():
    fp = compute_fingerprint("de-wind-fc", "missing_run", "2026-08-07T06:00:00+00:00")
    assert fp == "de-wind-fc|missing_run|2026-08-07T06:00:00+00:00"


@pytest.mark.parametrize("key,check", [("", "freshness"), ("k", ""), ("a|b", "c")])
def test_invalid_parts_rejected(key, check):
    with pytest.raises(ValueError):
        compute_fingerprint(key, check)
