"""Read the latest stored ESTIMATED score per manual portfolio. No scoring logic yet."""

from app.db.store import Store
from app.models.tables import ManualPortfolio, ScoreSnapshot
from app.schemas.api import PortfolioScore, ScoreResponse


def get_latest_scores(store: Store) -> ScoreResponse:
    out: list[PortfolioScore] = []
    for prow in store.select("manual_portfolios", order="name"):
        pf = ManualPortfolio.model_validate(prow)
        snaps = store.select(
            "score_snapshots",
            eq={"portfolio_id": str(pf.id)},
            order="snapshot_at",
            desc=True,
            limit=1,
        )
        out.append(
            PortfolioScore(
                portfolio_id=str(pf.id),
                portfolio_name=pf.name,
                latest=ScoreSnapshot.model_validate(snaps[0]) if snaps else None,
            )
        )
    return ScoreResponse(scores=out)
