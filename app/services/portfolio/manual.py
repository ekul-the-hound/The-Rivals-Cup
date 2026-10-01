"""Read manual portfolio records. Exposure here is COST BASIS, not mark-to-market."""

from app.db.store import Store
from app.models.enums import Direction
from app.models.tables import ManualPortfolio, ManualPosition, ManualTrade
from app.schemas.api import ManualPortfolioResponse, ManualPortfolioView

_NOTE = "Cost-basis exposure from manually recorded entry prices; not marked to market."


def get_manual_portfolios(store: Store) -> ManualPortfolioResponse:
    views: list[ManualPortfolioView] = []
    for prow in store.select("manual_portfolios", order="name"):
        pf = ManualPortfolio.model_validate(prow)
        positions = [
            ManualPosition.model_validate(r)
            for r in store.select(
                "manual_positions", eq={"portfolio_id": str(pf.id), "is_open": True}
            )
        ]
        trades = [
            ManualTrade.model_validate(r)
            for r in store.select(
                "manual_trades",
                eq={"portfolio_id": str(pf.id)},
                order="traded_at",
                desc=True,
                limit=20,
            )
        ]
        gross = net = 0.0
        for p in positions:
            notional = p.quantity * (p.avg_entry_price or 0.0)
            gross += notional
            net += notional if p.side == Direction.LONG else -notional
        views.append(
            ManualPortfolioView(
                portfolio=pf,
                open_positions=positions,
                recent_trades=trades,
                cost_basis_gross_exposure_usd=round(gross, 2),
                cost_basis_net_exposure_usd=round(net, 2),
                exposure_note=_NOTE,
            )
        )
    return ManualPortfolioResponse(portfolios=views)
