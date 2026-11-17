import contextlib
import os
import tempfile

_db_file = os.path.join(tempfile.gettempdir(), "aimo_network_api_test.sqlite3")
with contextlib.suppress(FileNotFoundError):
    os.remove(_db_file)
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_db_file}")
