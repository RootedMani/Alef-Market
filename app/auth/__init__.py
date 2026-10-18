"""Minimal accounts: password hashing and DB-backed sessions. Planned: Week 1.

- Passwords: argon2id via ``argon2-cffi`` (added in Week 1).
- Sessions: a random 32-byte token in an HttpOnly, SameSite=Lax cookie; the DB stores only
  its SHA-256, so a leaked DB can't be used to log in. Logout deletes the row.
"""
