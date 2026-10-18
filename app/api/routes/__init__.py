"""All JSON routers, mounted under /api by app.main."""

from app.api.routes import auth, backtest, briefing, market, system, trades

ROUTERS = (
    system.router,
    auth.router,
    market.router,
    briefing.router,
    backtest.router,
    trades.router,
)
