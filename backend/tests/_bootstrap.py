"""Set hermetic process paths before importing the application."""

import os
import tempfile

TEST_ROOT = tempfile.mkdtemp(prefix="flexsearch-tests-")
os.environ["APP_ENV"] = "test"
os.environ["LOG_PATH"] = os.path.join(TEST_ROOT, "logs")
os.environ["BACKEND_LOG_FILE"] = os.path.join(TEST_ROOT, "logs", "backend.log")
os.environ["APP_DATA_DIR"] = os.path.join(TEST_ROOT, "data")
