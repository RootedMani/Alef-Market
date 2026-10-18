# Alef Market

Persian-first AI copilot for crypto day-traders: before, during and after every trade.
**Decision support, not financial advice.**

## Quick start

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # once
uv sync                                           # Python 3.12 + dependencies
cp .env.example .env
make dev                                          # http://127.0.0.1:8000 · API docs at /docs
make check                                        # lint + tests (same as CI)
```

## Where things are

- `docs/architecture-v1.md`: architecture, contracts, API, storage, conventions, decisions
- `app/contracts/`: the data shapes every module and the frontend share
- `app/api/mocks.py`: example responses (`ALEF_MOCK_API=true`) for frontend work

The project scope lives in **Project Brief v1** (kept in the claude.ai project).
