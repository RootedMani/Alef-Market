"""Password hashing. Planned: Week 1."""

from __future__ import annotations


def hash_password(password: str) -> str:
    raise NotImplementedError("Week 1: argon2-cffi PasswordHasher().hash")


def verify_password(password_hash: str, password: str) -> bool:
    raise NotImplementedError("Week 1: return False on mismatch, never raise")
