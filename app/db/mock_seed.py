"""Seeds an InMemoryDB with the same reference universe as the SQL seed (for --mock runs/tests)."""

import uuid
from datetime import date, datetime, timedelta

from app.db.memory import InMemoryDB
from app.services.pairs.blackout import scoring_week

NS = uuid.UUID("00000000-0000-0000-0000-00000000c0de")
BENCH = [("SPY", None), ("QQQ", None), ("IWM", None)]
SECTOR_ETFS = {
    "XLB": "Materials", "XLC": "Communication Services", "XLE": "Energy", "XLF": "Financials",
    "XLI": "Industrials", "XLK": "Information Technology", "XLP": "Consumer Staples",
    "XLRE": "Real Estate", "XLU": "Utilities", "XLV": "Health Care", "XLY": "Consumer Discretionary",
}  # fmt: skip
EQUITIES = {
    "KO": ("Coca-Cola Co", "Consumer Staples", "The Coca-Cola Company"), "PEP": ("PepsiCo Inc", "Consumer Staples", "PepsiCo"),
    "HD": ("Home Depot Inc", "Consumer Discretionary", "Home Depot"), "LOW": ("Lowe's Companies Inc", "Consumer Discretionary", "Lowe's"),
    "V": ("Visa Inc", "Financials", "Visa Inc."), "MA": ("Mastercard Inc", "Financials", "Mastercard"),
    "XOM": ("Exxon Mobil Corp", "Energy", "ExxonMobil"), "CVX": ("Chevron Corp", "Energy", "Chevron Corporation"),
    "JPM": ("JPMorgan Chase & Co", "Financials", "JPMorgan Chase"), "BAC": ("Bank of America Corp", "Financials", "Bank of America"),
    "UPS": ("United Parcel Service Inc", "Industrials", "United Parcel Service"), "FDX": ("FedEx Corp", "Industrials", "FedEx"),
    "MRK": ("Merck & Co Inc", "Health Care", "Merck & Co."), "PFE": ("Pfizer Inc", "Health Care", "Pfizer"),
    "AMD": ("Advanced Micro Devices Inc", "Information Technology", "Advanced Micro Devices"), "INTC": ("Intel Corp", "Information Technology", "Intel"),
}  # fmt: skip
NASDAQ = {"PEP", "AMD", "INTC", "QQQ"}
# pair metadata: (relationship_quality, business_driver, macro_driver, explanation)
PAIR_META = {
    ("KO", "PEP"): (5, "Beverages & snacks", "consumer_defensive", "Global beverage duopoly; overlapping brands, channels and input costs."),
    ("HD", "LOW"): (5, "Home improvement retail", "housing_consumer_cyclical", "Two dominant U.S. home-improvement retailers with near-identical formats."),
    ("V", "MA"): (5, "Payment networks", "consumer_payments_volume", "Global card networks with the same take-rate and volume drivers."),
    ("XOM", "CVX"): (5, "Integrated oil & gas", "oil_price", "U.S. integrated majors driven by crude and refining margins."),
    ("JPM", "BAC"): (4, "Money-center banks", "rates_credit", "Largest U.S. universal banks; shared rate and credit drivers."),
    ("UPS", "FDX"): (4, "Parcel & freight delivery", "freight_industrial_cycle", "Global parcel carriers exposed to e-commerce volume and the freight cycle."),
    ("MRK", "PFE"): (4, "Large-cap pharmaceuticals", "pharma_policy_pipeline", "Large-cap pharma exposed to pricing policy and patent cliffs."),
    ("AMD", "INTC"): (3, "x86 CPUs & data-center silicon", "semis_cycle", "x86 competitors; diverging execution makes the relationship looser."),
}  # fmt: skip
PAIRS = [
    ("KO", "PEP"),
    ("HD", "LOW"),
    ("V", "MA"),
    ("XOM", "CVX"),
    ("JPM", "BAC"),
    ("UPS", "FDX"),
    ("MRK", "PFE"),
    ("AMD", "INTC"),
]


def sid(ticker: str) -> str:
    return str(uuid.uuid5(NS, ticker))


def build_mock_db(today: date, now: datetime) -> InMemoryDB:
    securities = [
        {
            "id": sid(t),
            "ticker": t,
            "name": t,
            "is_etf": True,
            "security_type": "ETF",
            "exchange": "NYSE Arca",
            "is_benchmark": True,
            "is_active": True,
            "sector": None,
        }
        for t, _ in BENCH
    ]
    securities += [
        {
            "id": sid(t),
            "ticker": t,
            "name": t,
            "is_etf": True,
            "security_type": "ETF",
            "exchange": "NYSE Arca",
            "is_benchmark": False,
            "is_active": True,
            "sector": s,
        }
        for t, s in SECTOR_ETFS.items()
    ]
    securities += [{"id": sid(t), "ticker": t, "name": n, "is_etf": False, "security_type": "EQUITY", "exchange": "NASDAQ" if t in NASDAQ else "NYSE", "country": "US", "leverage_factor": 1, "unresolved_corporate_action": False, "wsr_eligibility": "UNVERIFIED", "is_benchmark": False, "is_active": True, "sector": s} for t, (n, s, _) in EQUITIES.items()]  # fmt: skip
    pairs, members = [], []
    for a, b in PAIRS:
        pid = str(uuid.uuid5(NS, f"{a}/{b}"))
        pairs.append(
            {
                "id": pid,
                "name": f"{a} / {b}",
                "sector": EQUITIES[a][1],
                "status": "CANDIDATE",
                "rationale": "sample",
                "relationship_quality": PAIR_META[(a, b)][0],
                "business_driver": PAIR_META[(a, b)][1],
                "macro_driver": PAIR_META[(a, b)][2],
                "relationship_explanation": PAIR_META[(a, b)][3],
                "thesis_basis": "RELATIVE_VALUE",
            }
        )
        members += [
            {"pair_id": pid, "security_id": sid(a), "role": "A"},
            {"pair_id": pid, "security_id": sid(b), "role": "B"},
        ]
    inv = {v: k for k, v in SECTOR_ETFS.items()}
    mappings = [
        {"sector": s, "etf_security_id": sid(inv[s]), "is_primary": True}
        for s in {v[1] for v in EQUITIES.values()}
    ]
    companies = [
        {"security_id": sid(t), "wiki_title": w, "legal_name": n}
        for t, (n, _, w) in EQUITIES.items()
    ]
    start, _ = scoring_week(today)
    verified = now.isoformat()
    blackout = [{"security_id": sid(t), "week_start": start.isoformat(), "manually_verified_at": verified, "source_url": "https://example.com/mock-calendar"} for t in ("KO", "PEP", "V", "MA")]  # fmt: skip
    blackout += [
        {"security_id": sid("HD"), "week_start": start.isoformat(), "earnings_date_if_known": (start + timedelta(days=2)).isoformat(), "manually_verified_at": verified, "event_risk_notes": "MOCK: reports mid-week", "source_url": "https://example.com/mock-calendar"},
        {"security_id": sid("AMD"), "week_start": start.isoformat(), "known_major_event_date": (start + timedelta(days=3)).isoformat(), "manually_verified_at": verified, "event_risk_notes": "MOCK: product event", "source_url": "https://example.com/mock-event"},
    ]  # fmt: skip
    overrides = [
        {
            "id": str(uuid.uuid5(NS, "ov")),
            "pair_id": pairs[7]["id"],
            "week_start": start.isoformat(),
            "reason": "MOCK: event is a product demo, not a binary catalyst",
        }
    ]
    return InMemoryDB(
        {
            "system_control_state": [{"id": 1, "mode": "RESEARCH_ONLY", "signal_sending_enabled": False, "provider_ingestion_enabled": True}],
            "securities": securities, "peer_pairs": pairs, "peer_pair_members": members,
            "sector_etf_mappings": mappings, "companies": companies,
            "security_event_blackouts": blackout, "pair_blackout_overrides": overrides,
        }
    )  # fmt: skip
