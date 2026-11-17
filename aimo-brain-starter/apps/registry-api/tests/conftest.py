import contextlib
import os
import tempfile

_db_file = os.path.join(tempfile.gettempdir(), "aimo_registry_api_test.sqlite3")
with contextlib.suppress(FileNotFoundError):
    os.remove(_db_file)
os.environ.setdefault("REGISTRY_DATABASE_URL", f"sqlite+aiosqlite:///{_db_file}")
os.environ.setdefault("SERVICE_CREDENTIAL_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("NETWORK_INTERNAL_SERVICE_KEY", "test-internal-key")
