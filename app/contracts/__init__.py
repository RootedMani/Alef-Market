"""Contracts: the typed data shapes every module and the frontend agree on.

Rules:
- Contracts import only pydantic, pandas (frames.py) and each other. Nothing else.
- Change a contract only on purpose, and update docs/architecture-v1.md in the same commit.
- Import from the submodule: ``from app.contracts.market import Candle``.
"""
