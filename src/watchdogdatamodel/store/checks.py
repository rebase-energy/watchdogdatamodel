"""Check catalog (spec §3.2; physical table check_definition)."""
from psycopg.types.json import Jsonb

from ..models import CheckDef


def upsert_check(conn, *, id, name, description=None, dimension=None,
                 default_params=None, contract=None, enabled=True) -> CheckDef:
    row = conn.execute(
        """
        INSERT INTO check_definition (id, name, description, dimension, default_params, contract, enabled)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name, description = EXCLUDED.description,
            dimension = EXCLUDED.dimension, default_params = EXCLUDED.default_params,
            contract = EXCLUDED.contract,
            enabled = EXCLUDED.enabled, updated_at = now()
        RETURNING *
        """,
        (id, name, description, dimension, Jsonb(default_params or {}),
         Jsonb(contract) if contract is not None else None, enabled),
    ).fetchone()
    return CheckDef(**row)


def list_checks(conn, enabled: bool | None = True) -> list[CheckDef]:
    q, params = "SELECT * FROM check_definition", []
    if enabled is not None:
        q += " WHERE enabled = %s"
        params.append(enabled)
    q += " ORDER BY id"
    return [CheckDef(**r) for r in conn.execute(q, params).fetchall()]
