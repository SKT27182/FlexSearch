#!/usr/bin/env python3
"""
Basic load-smoke script for FlexSearch chat / health endpoints.

Usage (from repo root, with API running and a valid token):

  export FLEXSEARCH_BASE_URL=http://127.0.0.1:8889
  export FLEXSEARCH_TOKEN=...
  export FLEXSEARCH_PROJECT_ID=...
  python backend/scripts/load_smoke.py --concurrency 4 --requests 20

This is intentionally lightweight (httpx + asyncio) — not a full k6/locust suite.
See backend/docs/ops/README.md for interpreting results.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time

import httpx


async def _one(
    client: httpx.AsyncClient,
    *,
    path: str,
    method: str,
    json_body: dict | None,
    headers: dict[str, str],
) -> tuple[int, float]:
    started = time.perf_counter()
    if method == "GET":
        resp = await client.get(path, headers=headers)
    else:
        resp = await client.post(path, headers=headers, json=json_body)
    return resp.status_code, (time.perf_counter() - started) * 1000


async def run(args: argparse.Namespace) -> int:
    base = args.base_url.rstrip("/")
    headers = {}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    latencies: list[float] = []
    statuses: list[int] = []

    sem = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(base_url=base, timeout=60.0) as client:

        async def worker(i: int) -> None:
            async with sem:
                if args.endpoint == "health":
                    code, ms = await _one(
                        client, path="/health", method="GET", json_body=None, headers={}
                    )
                else:
                    body = {
                        "project_id": args.project_id,
                        "query": f"load smoke question {i}",
                        "persist": False,
                    }
                    code, ms = await _one(
                        client,
                        path="/api/chat/query",
                        method="POST",
                        json_body=body,
                        headers=headers,
                    )
                statuses.append(code)
                latencies.append(ms)

        await asyncio.gather(*(worker(i) for i in range(args.requests)))

    ok = sum(1 for s in statuses if 200 <= s < 300)
    print(f"requests={args.requests} concurrency={args.concurrency}")
    print(f"ok={ok} errors={args.requests - ok}")
    if latencies:
        print(
            f"latency_ms p50={statistics.median(latencies):.1f} "
            f"mean={statistics.mean(latencies):.1f} "
            f"max={max(latencies):.1f}"
        )
    return 0 if ok == args.requests else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="FlexSearch load smoke")
    parser.add_argument(
        "--base-url",
        default=os.getenv("FLEXSEARCH_BASE_URL", "http://127.0.0.1:8889"),
    )
    parser.add_argument("--token", default=os.getenv("FLEXSEARCH_TOKEN", ""))
    parser.add_argument("--project-id", default=os.getenv("FLEXSEARCH_PROJECT_ID", ""))
    parser.add_argument(
        "--endpoint",
        choices=("health", "chat"),
        default="health",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--requests", type=int, default=20)
    args = parser.parse_args()
    if args.endpoint == "chat" and (not args.token or not args.project_id):
        parser.error("chat endpoint requires --token and --project-id")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
