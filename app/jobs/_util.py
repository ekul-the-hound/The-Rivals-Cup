from typing import Any

from app.db.writer import DB


def active_securities(db: DB) -> list[dict[str, Any]]:
    return db.select("securities", eq={"is_active": True}, order="ticker")


def load_bars(db: DB, security_id: str, limit: int = 130) -> list[dict[str, Any]]:
    rows = db.select(
        "market_bars",
        eq={"security_id": security_id, "timeframe": "1D"},
        order="bar_date",
        desc=True,
        limit=limit,
    )
    return sorted(rows, key=lambda r: str(r["bar_date"]))


def short_name(name: str | None) -> str:
    import re

    return re.sub(r"\b(Inc|Corp|Corporation|Co|Company|Ltd|plc|The)\b\.?,?", "", name or "").strip(
        " ,."
    )
