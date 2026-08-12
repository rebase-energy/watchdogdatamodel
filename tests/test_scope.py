import pytest

from watchdogdatamodel.scope import scope_covers, validate_scope

LBL = {"zone": "SE-SE1", "data_type": "production", "source": "entsoe"}


def test_all_all_covers_everything():
    s = validate_scope({"series": "all", "checks": "all"})
    assert scope_covers(s, series_id="x", labels=LBL, check_id="freshness")


def test_label_filter_subset_match():
    s = {"series": {"labels": {"zone": "SE-SE1"}}, "checks": "all"}
    assert scope_covers(s, series_id="x", labels=LBL, check_id="freshness")
    assert not scope_covers(s, series_id="x", labels={"zone": "FI"}, check_id="freshness")


def test_label_list_means_membership():
    s = {"series": {"labels": {"zone": ["SE-SE1", "FI"]}}, "checks": "all"}
    assert scope_covers(s, series_id="x", labels=LBL, check_id="freshness")
    assert not scope_covers(s, series_id="x", labels={"zone": "DE-LU"}, check_id="freshness")


def test_id_list_and_check_list():
    s = {"series": {"ids": ["a", "b"]}, "checks": ["gross_range"]}
    assert scope_covers(s, series_id="a", labels={}, check_id="gross_range")
    assert not scope_covers(s, series_id="c", labels={}, check_id="gross_range")
    assert not scope_covers(s, series_id="a", labels={}, check_id="freshness")


def test_keys_scope_validates():
    s = validate_scope({"series": {"keys": ["SE-SE1|production|entsoe"]}, "checks": "all"})
    assert s["series"] == {"keys": ["SE-SE1|production|entsoe"]}


def test_keys_covers_by_key_true_and_false():
    s = {"series": {"keys": ["SE-SE1|production|entsoe"]}, "checks": "all"}
    assert scope_covers(
        s, series_id="x", labels=LBL, check_id="freshness", series_key="SE-SE1|production|entsoe"
    )
    assert not scope_covers(
        s, series_id="x", labels=LBL, check_id="freshness", series_key="FI|production|entsoe"
    )


def test_keys_scope_does_not_cover_when_caller_omits_series_key():
    # series_key defaults to None; a keys-scope must not accidentally match that.
    s = {"series": {"keys": ["SE-SE1|production|entsoe"]}, "checks": "all"}
    assert not scope_covers(s, series_id="x", labels=LBL, check_id="freshness")


def test_label_scopes_unaffected_by_series_key_param():
    s = {"series": {"labels": {"zone": "SE-SE1"}}, "checks": "all"}
    assert scope_covers(
        s, series_id="x", labels=LBL, check_id="freshness", series_key="anything"
    )
    assert not scope_covers(
        s, series_id="x", labels={"zone": "FI"}, check_id="freshness", series_key="anything"
    )


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"series": "all"},
        {"series": "some", "checks": "all"},
        {"series": {"labels": {}, "ids": []}, "checks": "all"},
        {"series": {"labels": {}, "keys": ["a"]}, "checks": "all"},
        {"series": "all", "checks": "none"},
    ],
)
def test_invalid_scopes_rejected(bad):
    with pytest.raises(ValueError):
        validate_scope(bad)


@pytest.mark.parametrize(
    "bad_keys",
    [
        [],
        ["a", 1],
        ["a", ""],
        "a",
        None,
    ],
)
def test_keys_selector_rejects_bad_shapes(bad_keys):
    with pytest.raises(ValueError):
        validate_scope({"series": {"keys": bad_keys}, "checks": "all"})
