import contextlib
import os
import tempfile

_db_file = os.path.join(tempfile.gettempdir(), "aimo_supplier_mcp_test.sqlite3")
with contextlib.suppress(FileNotFoundError):
    os.remove(_db_file)
os.environ.setdefault("SUPPLIER_MCP_DATABASE_URL", f"sqlite+aiosqlite:///{_db_file}")
os.environ.setdefault("SERVICE_CREDENTIAL_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("SUPPLIER_MCP_ORGANIZATION_ID", "org_fictional_wholesale_01")
