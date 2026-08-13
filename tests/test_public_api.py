def test_flat_public_api():
    import watchdogdatamodel as w

    for name in (
        "compute_fingerprint", "scope_covers", "validate_scope",
        "dump_timeseries", "load_timeseries", "excerpt", "models", "store",
        "query",
    ):
        assert hasattr(w, name), name
