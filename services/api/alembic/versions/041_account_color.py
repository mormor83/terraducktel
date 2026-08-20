"""Cloud accounts: optional `color` label for at-a-glance run attribution.

One nullable VARCHAR(16) on each of the four cloud-account tables. The value is
a token from `app.services.account_colors.ACCOUNT_COLORS` (red/orange/yellow/
green/blue/purple/brown/gray), NOT a hex — see that module for why.

The API claims a colour at creation time (first one unused in the BU), so
colours are guaranteed distinct up to 8 accounts per BU and never shift
afterwards. This revision backfills every pre-existing row the same way, so
nobody has to visit Settings to get a usable palette. NULL survives only for
rows inserted outside the API (seed scripts, direct SQL), which the API paints
via a hash fallback.

Deliberately no CHECK constraint / enum type: the allowed set is validated in
the Pydantic layer, and a DB-level enum would need its own migration every time
a colour is appended.

Revision ID: 041_account_color
Revises: 040_azure_state_storage
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa

revision = "041_account_color"
down_revision = "040_azure_state_storage"
branch_labels = None
depends_on = None

_TABLES = ("aws_accounts", "azure_subscriptions", "gcp_projects", "k8s_clusters")


# Snapshot of app.services.account_colors.ACCOUNT_COLORS at this revision.
# Inlined rather than imported: a migration must keep producing the same result
# forever, even after the palette in the application code grows.
_PALETTE = ("red", "orange", "yellow", "green", "blue", "purple", "brown", "gray")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("color", sa.String(length=16), nullable=True))
    _backfill()


def _backfill() -> None:
    """Give every existing account a colour, distinct within its Business Unit.

    Walks all four provider tables together (a colour reused across providers
    is as confusing as one reused within a provider) in a deterministic order —
    BU, then table, then name — and round-robins the palette. Ordering by name
    rather than created_at keeps the result reproducible across environments
    whose rows were created in a different sequence.
    """
    bind = op.get_bind()
    rows = []
    for table_rank, table in enumerate(_TABLES):
        result = bind.execute(sa.text(f"SELECT id, business_unit_id, name FROM {table}"))  # noqa: S608
        for r in result.mappings():
            rows.append((r["business_unit_id"] or "", table_rank, r["name"] or "", r["id"], table))

    rows.sort(key=lambda r: r[:4])
    seen_per_bu: dict[str, int] = {}
    for bu_id, _rank, _name, row_id, table in rows:
        n = seen_per_bu.get(bu_id, 0)
        seen_per_bu[bu_id] = n + 1
        bind.execute(
            sa.text(f"UPDATE {table} SET color = :c WHERE id = :i"),  # noqa: S608
            {"c": _PALETTE[n % len(_PALETTE)], "i": row_id},
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "color")
