"""
Diagnostics self-report helper for Map Control Server.

Exposes a uniform JSON snapshot describing this service's identity, env
keys (presence + fingerprint only — never values), reachability of
configured peers/externals, DNS resolution from this process's
perspective, and process-level runtime metrics. Consumed by any
external diagnostics aggregator via the `/__diag` endpoint.

Self-contained: stdlib-only (Python 3.10+); psutil/httpx are used
opportunistically if installed but never required. Safe to drop into
any FastAPI/uvicorn service.
"""
from __future__ import annotations

import asyncio
import os
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

SCHEMA_VERSION = "1"


def _fingerprint(value: str | None) -> str | None:
    if not value or len(value) < 4:
        return None
    return f"····{value[-4:]}"


def env_keys_snapshot(
    keys: Iterable[str],
    code_values: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the `env` section — presence + fingerprint only, never the value.

    If `code_values` is provided, each entry additionally reports a
    `code_fingerprint` / `code_present` / `override` triple, used by
    callers that want to surface settings-module constants shadowing
    their env (override=True when env and code both exist but differ).
    """
    code_values = code_values or {}
    result: dict[str, dict[str, Any]] = {}
    for key in keys:
        val = os.environ.get(key) or ""
        entry: dict[str, Any] = {
            "present": bool(val),
            "length": len(val) if val else None,
            "fingerprint": _fingerprint(val) if val else None,
            "source": "env" if val else None,
        }
        if key in code_values:
            code_val = code_values.get(key) or ""
            entry["code_present"] = bool(code_val)
            entry["code_fingerprint"] = _fingerprint(code_val) if code_val else None
            entry["override"] = bool(val and code_val and val != code_val)
        result[key] = entry
    return result


def _extract_host(target: str) -> str | None:
    if not target:
        return None
    t = target.strip()
    if "://" in t:
        t = urlparse(t).hostname or ""
    if ":" in t:
        t = t.split(":")[0]
    return t or None


def resolve_dns(targets: Iterable[str]) -> dict[str, str | None]:
    """Resolve each hostname via the container's DNS, keyed by the original input."""
    out: dict[str, str | None] = {}
    for target in targets:
        host = _extract_host(target)
        if not host or host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            out[target] = host
            continue
        try:
            out[target] = socket.gethostbyname(host)
        except Exception:
            out[target] = None
    return out


async def probe_url(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """Single HTTP GET probe — returns a ProbeResult-shaped dict."""
    if not url:
        return {
            "url": "",
            "reachable": False,
            "status_code": None,
            "latency_ms": None,
            "error": "no url configured",
        }

    t0 = time.perf_counter()
    try:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
            status_code = resp.status_code
        except ImportError:
            import requests
            resp = await asyncio.to_thread(requests.get, url, timeout=timeout)
            status_code = resp.status_code
        elapsed = round((time.perf_counter() - t0) * 1000)
        ok = status_code < 500
        return {
            "url": url,
            "reachable": ok,
            "status_code": status_code,
            "latency_ms": elapsed,
            "error": None if ok else f"HTTP {status_code}",
        }
    except Exception as e:
        elapsed = round((time.perf_counter() - t0) * 1000)
        return {
            "url": url,
            "reachable": False,
            "status_code": None,
            "latency_ms": elapsed,
            "error": f"{type(e).__name__}: {e}"[:200],
        }


async def probe_all(targets: dict[str, str], timeout: float = 5.0) -> dict[str, dict[str, Any]]:
    """Probe many URLs in parallel; keys are display names."""
    if not targets:
        return {}
    names = list(targets.keys())
    results = await asyncio.gather(
        *[probe_url(targets[n], timeout=timeout) for n in names],
        return_exceptions=False,
    )
    return dict(zip(names, results))


def _git_fallback(args: list[str]) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", *args], stderr=subprocess.DEVNULL, timeout=2
        )
        return out.decode().strip() or None
    except Exception:
        return None


def identity_block(service: str, started_at: float, language: str) -> dict[str, Any]:
    """Build the `identity` section — env vars baked at build time, with git fallback."""
    commit_hash = os.environ.get("COMMIT_HASH") or _git_fallback(["log", "-1", "--format=%h"]) or "unknown"
    commit_time = os.environ.get("COMMIT_TIME") or _git_fallback(["log", "-1", "--format=%cI"]) or ""
    branch = os.environ.get("COMMIT_BRANCH") or _git_fallback(["rev-parse", "--abbrev-ref", "HEAD"]) or ""
    build_time = os.environ.get("BUILD_TIME") or None
    uptime_s = round(time.time() - started_at, 1)
    return {
        "service": service,
        "container_id": socket.gethostname() or None,
        "image_tag": os.environ.get("IMAGE_TAG") or None,
        "commit_hash": commit_hash,
        "commit_time": commit_time,
        "branch": branch,
        "build_time": build_time,
        "started_at": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(),
        "uptime_seconds": uptime_s,
        "language": language,
        "platform": f"{platform.system()} {platform.machine()}",
    }


def runtime_block(started_at: float) -> dict[str, Any]:
    """Build the `runtime` section — process RAM via psutil if available."""
    uptime_s = max(0, int(time.time() - started_at))
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)
    ram_mb: float = 0.0
    try:
        import psutil  # type: ignore
        ram_mb = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
    except Exception:
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        ram_mb = round(int(line.split()[1]) / 1024, 1)
                        break
        except Exception:
            pass
    return {
        "uptime_human": f"{h}h {m}m {s}s",
        "ram_mb": ram_mb,
        "heap_mb": None,
        "process_pid": os.getpid(),
    }


async def build_diag(
    service: str,
    started_at: float,
    env_key_names: Iterable[str],
    peer_urls: dict[str, str],
    external_urls: dict[str, str],
    dns_hosts: Iterable[str] | None = None,
    service_specific: dict[str, Any] | None = None,
    language: str | None = None,
    timeout: float = 5.0,
    code_values: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Assemble a DiagPayload for one container.

    Each container supplies its own meaningful env-key list, peer URLs, and
    external URLs — diagnostics is per-container by design so each report
    reflects what *this* container can see.
    """
    warnings: list[str] = []
    if language is None:
        language = f"python {sys.version.split()[0]}"

    peers, external = await asyncio.gather(
        probe_all(peer_urls, timeout=timeout),
        probe_all(external_urls, timeout=timeout),
    )

    dns_targets = list(dns_hosts) if dns_hosts is not None else list(peer_urls.values())
    dns_table = resolve_dns(dns_targets)

    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "identity": identity_block(service, started_at, language),
        "env": env_keys_snapshot(env_key_names, code_values=code_values),
        "network": {
            "dns": dns_table,
            "peers": peers,
            "external": external,
        },
        "runtime": runtime_block(started_at),
        "service_specific": service_specific or {},
        "warnings": warnings,
    }
