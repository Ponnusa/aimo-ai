import os
import tempfile

_db_file = os.path.join(tempfile.gettempdir(), "aimo_supplier_agent_test.sqlite3")
os.environ.setdefault("SUPPLIER_DATABASE_URL", f"sqlite+aiosqlite:///{_db_file}")
