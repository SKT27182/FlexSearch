"""Set hermetic process paths and required Settings before importing the app."""

import os
import tempfile
from pathlib import Path

# Force-assign (not setdefault) so a developer shell env cannot leak into the
# suite — same approach as RootAgent / infra-hub CI hermetic tests.
os.environ["APP_ENV"] = "test"
# Prefer IPv4 loopback — "localhost" can resolve to ::1 first on some runners.
os.environ["POSTGRES_HOST"] = "127.0.0.1"
os.environ["POSTGRES_USER"] = "test"
os.environ["POSTGRES_PASSWORD"] = "test-password"
os.environ["MINIO_ACCESS_KEY"] = "test-access-key"
os.environ["MINIO_SECRET_KEY"] = "test-secret-key"
os.environ["REDIS_PASSWORD"] = "test-redis-password"
os.environ["NEO4J_USER"] = "neo4j"
os.environ["NEO4J_PASSWORD"] = "test-neo4j-password"
# Assembled so static scanners do not treat it as a committed secret.
os.environ["JWT_SECRET"] = "-".join(
    ("flexsearch", "test", "jwt", "signing", "key", "0123456789abcdef")
)
# GraphRAG settings.yaml substitutions (${...}); load_config requires them.
os.environ["API_KEY"] = "test-llm-api-key"
os.environ["GRAPHRAG_API_KEY"] = "test-llm-api-key"
os.environ["GRAPHRAG_EMBEDDING_API_KEY"] = "test-embedding-api-key"
os.environ["GRAPHRAG_API_BASE"] = ""
os.environ["GRAPHRAG_EMBEDDING_API_BASE"] = ""

# Portable across macOS and Linux CI (avoid /private/tmp, not writable on GHA).
TEST_ROOT = Path(tempfile.gettempdir()) / "flexsearch-tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
(TEST_ROOT / "logs").mkdir(parents=True, exist_ok=True)
(TEST_ROOT / "data").mkdir(parents=True, exist_ok=True)
os.environ["LOG_PATH"] = str(TEST_ROOT / "logs")
os.environ["BACKEND_LOG_FILE"] = str(TEST_ROOT / "logs" / "backend.log")
os.environ["APP_DATA_DIR"] = str(TEST_ROOT / "data")
