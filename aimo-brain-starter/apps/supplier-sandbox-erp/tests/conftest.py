import contextlib
import os
import tempfile

_db_file = os.path.join(tempfile.gettempdir(), "aimo_supplier_sandbox_erp_test.sqlite3")
with contextlib.suppress(FileNotFoundError):
    os.remove(_db_file)
os.environ.setdefault("SUPPLIER_SANDBOX_ERP_DATABASE_URL", f"sqlite+aiosqlite:///{_db_file}")
os.environ.setdefault("SERVICE_CREDENTIAL_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("SUPPLIER_SANDBOX_ERP_ORGANIZATION_ID", "org_fictional_wholesale_01")
os.environ.setdefault("ENVIRONMENT", "test")
