"""Declared run scope and the coverage rule's matcher (spec §3.3).

healthy = covered by a completed run + no open issue. This module answers
the "covered" half: did a run's declared scope include (series, check)?
"""


def validate_scope(scope: dict) -> dict:
    if set(scope) != {"series", "checks"}:
        raise ValueError("scope must have exactly the keys 'series' and 'checks'")
    series = scope["series"]
    if series != "all":
        if not isinstance(series, dict) or len(series) != 1:
            raise ValueError("scope['series'] must be 'all', {'labels': {...}} or {'ids': [...]}")
        (kind, value), = series.items()
        if kind == "labels":
            if not isinstance(value, dict):
                raise ValueError("scope['series']['labels'] must be a dict")
            # A label value may be a scalar (equality) or a list (membership).
        elif kind == "ids":
            if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
                raise ValueError("scope['series']['ids'] must be a list of strings")
        else:
            raise ValueError(f"unknown series selector {kind!r}")
    checks = scope["checks"]
    if checks != "all" and (
        not isinstance(checks, list) or not all(isinstance(c, str) for c in checks)
    ):
        raise ValueError("scope['checks'] must be 'all' or a list of check ids")
    return scope


def scope_covers(scope: dict, *, series_id: str, labels: dict, check_id: str) -> bool:
    validate_scope(scope)
    series = scope["series"]
    if series != "all":
        (kind, value), = series.items()
        if kind == "ids" and series_id not in value:
            return False
        if kind == "labels":
            for k, v in value.items():
                if isinstance(v, list):
                    if labels.get(k) not in v:
                        return False
                elif labels.get(k) != v:
                    return False
    checks = scope["checks"]
    if checks != "all" and check_id not in checks:
        return False
    return True
