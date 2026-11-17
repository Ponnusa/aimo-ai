"""Shared environment setup for top-level cross-app tests.

Each service gets its own temp-file SQLite database so that
tests/integration, tests/security and tests/e2e can boot all three
services in the same process without needing Postgres, while still proving
each one uses a *different* database file (never the same one).
"""

import contextlib
import os
import tempfile

_tmp = tempfile.gettempdir()
_db_files = [
    f"{_tmp}/aimo_top_network.sqlite3",
    f"{_tmp}/aimo_top_buyer.sqlite3",
    f"{_tmp}/aimo_top_supplier.sqlite3",
]
for _f in _db_files:
    with contextlib.suppress(FileNotFoundError):
        os.remove(_f)

os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_db_files[0]}")
os.environ.setdefault("BUYER_DATABASE_URL", f"sqlite+aiosqlite:///{_db_files[1]}")
os.environ.setdefault("SUPPLIER_DATABASE_URL", f"sqlite+aiosqlite:///{_db_files[2]}")
