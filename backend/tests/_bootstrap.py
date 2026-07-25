"""Set hermetic process paths and required Settings before importing the app."""

import os
import tempfile

TEST_ROOT = tempfile.mkdtemp(prefix="flexsearch-tests-")
os.environ["APP_ENV"] = "test"
os.environ["LOG_PATH"] = os.path.join(TEST_ROOT, "logs")
os.environ["BACKEND_LOG_FILE"] = os.path.join(TEST_ROOT, "logs", "backend.log")
os.environ["APP_DATA_DIR"] = os.path.join(TEST_ROOT, "data")

# Settings requires these fields with no defaults. Use setdefault so a real
# .env or CI env wins, while pytest stays hermetic without either.
_REQUIRED_SETTINGS_DEFAULTS = {
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test-password",
    "MINIO_ACCESS_KEY": "test-access-key",
    "MINIO_SECRET_KEY": "test-secret-key",
    "REDIS_PASSWORD": "test-redis-password",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "test-neo4j-password",
    "JWT_SECRET": "test-jwt-secret-at-least-32-characters-long",
    # GraphRAG settings.yaml substitutions (${...}); load_config requires them.
    "API_KEY": "test-llm-api-key",
    "GRAPHRAG_API_KEY": "test-llm-api-key",
    "GRAPHRAG_EMBEDDING_API_KEY": "test-embedding-api-key",
    "GRAPHRAG_API_BASE": "",
    "GRAPHRAG_EMBEDDING_API_BASE": "",
}
for _key, _value in _REQUIRED_SETTINGS_DEFAULTS.items():
    os.environ.setdefault(_key, _value)
