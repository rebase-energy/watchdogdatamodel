"""Issue fingerprints: the dedup identity (spec §3.4)."""


def compute_fingerprint(series_key: str, check_id: str, discriminator: str | None = None) -> str:
    """Build the fingerprint for a check-origin issue.

    ``series_key + '|' + check_id`` plus an optional product discriminator
    (e.g. a forecast run's knowledge_time on OVERLAPPING series).
    """
    parts = [series_key, check_id] + ([discriminator] if discriminator is not None else [])
    for part in parts[:2]:
        if not part:
            raise ValueError("series_key and check_id must be non-empty")
    if any("|" in p for p in parts[:2]):
        raise ValueError("'|' is the fingerprint separator and cannot appear in parts")
    return "|".join(parts)
