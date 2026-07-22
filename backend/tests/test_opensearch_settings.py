"""Settings: OpenSearch replaces Qdrant."""

from app.core.config import Settings


def test_opensearch_index_name_from_prefix() -> None:
    s = Settings(
        postgres_user="u",
        postgres_password="p",
        minio_access_key="a",
        minio_secret_key="s",
        jwt_secret="secret",
        opensearch_index_prefix="flexsearch",
        opensearch_index_name="chunks",
    )
    assert s.opensearch_index == "flexsearch_chunks"
    assert "opensearch" in s.admin_urls
    assert "qdrant" not in s.admin_urls


def test_celery_broker_defaults_to_redis() -> None:
    s = Settings(
        postgres_user="u",
        postgres_password="p",
        minio_access_key="a",
        minio_secret_key="s",
        jwt_secret="secret",
        redis_host="127.0.0.1",
        redis_port=63791,
        redis_password="pw",
        redis_db=0,
    )
    assert s.redis_url is not None
    assert s.celery_broker_url == s.redis_url
    assert s.celery_result_backend == s.redis_url


def test_celery_routes_six_tasks_across_four_queues() -> None:
    from app.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert len(routes) == 6
    queues = {entry["queue"] for entry in routes.values()}
    assert queues == {"ingest", "graph", "summary", "default"}
    assert celery_app.conf.beat_schedule["dispatch-outbox"]["schedule"] == 5.0
    assert celery_app.conf.beat_schedule["dispatch-outbox"]["options"] == {
        "expires": 5.0
    }
    assert celery_app.conf.worker_hijack_root_logger is False
    assert celery_app.conf.worker_redirect_stdouts is False
