"""Structured logging + business-metric helpers.

Keeps the dependency footprint zero: no structlog / prometheus_client. We emit
NDJSON to stdout (one event per line) which any log aggregator can ingest, and
we expose the metrics through the existing `/metrics` endpoint by extending a
shared registry dict.

Why not Prometheus client?
    - The API already exports a Prom text endpoint with hand-rolled counters
      (see `app/main.py`). Adding the official client would require coordinating
      registries across the worker + reaper + drift detector; keeping it simple
      avoids that fan-out while still emitting valid Prom text.
    - When you outgrow this, swap the registry below for `prometheus_client`.

Observed metrics today
    tdt_run_duration_seconds_sum/_count{phase,status}   — histogram-ish
    tdt_run_queue_depth_gauge                           — gauge from run_jobs
    tdt_state_lock_wait_seconds_sum/_count              — counter+sum
    tdt_drift_age_seconds_gauge                         — oldest drift report
    tdt_approval_pending_seconds_gauge                  — oldest awaiting_approval
    tdt_executor_failures_total                         — counter
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any


# ─── Logging ──────────────────────────────────────────────────────────────


class JSONFormatter(logging.Formatter):
    """One-line JSON per log record. Keys mirror the standard fields plus any
    `extra=` dict the caller passes. Exceptions land under `exc_info`."""

    _RESERVED = {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in self._RESERVED and not k.startswith("_"):
                # Last-ditch fallback for non-JSON-serialisable objects
                try:
                    json.dumps(v)
                    payload[k] = v
                except TypeError:
                    payload[k] = repr(v)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging() -> None:
    """Idempotent. Replaces uvicorn's default handler with the JSON formatter."""
    root = logging.getLogger()
    # Strip whatever uvicorn / basicConfig left behind
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # uvicorn's loggers should propagate to root; force them to not double-emit
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


# ─── Metrics registry ─────────────────────────────────────────────────────


class _Counter:
    __slots__ = ("name", "labels", "value")

    def __init__(self, name: str, labels: tuple[tuple[str, str], ...] = ()) -> None:
        self.name = name
        self.labels = labels
        self.value = 0.0

    def inc(self, amount: float = 1.0) -> None:
        self.value += amount


class _Gauge(_Counter):
    def set(self, value: float) -> None:
        self.value = value


# Single in-process registry. Worker + reaper + API request handlers all share.
_COUNTERS: dict[tuple[str, tuple[tuple[str, str], ...]], _Counter] = {}
_GAUGES: dict[tuple[str, tuple[tuple[str, str], ...]], _Gauge] = {}
_HIST_SUM: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_HIST_COUNT: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}


def _key(name: str, labels: dict[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    pairs = tuple(sorted((labels or {}).items()))
    return (name, pairs)


def counter_inc(name: str, labels: dict[str, str] | None = None, amount: float = 1.0) -> None:
    k = _key(name, labels)
    c = _COUNTERS.get(k)
    if c is None:
        c = _Counter(name, k[1])
        _COUNTERS[k] = c
    c.inc(amount)


def gauge_set(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    k = _key(name, labels)
    g = _GAUGES.get(k)
    if g is None:
        g = _Gauge(name, k[1])
        _GAUGES[k] = g
    g.set(value)


def histogram_observe(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    k = _key(name, labels)
    _HIST_SUM[k] = _HIST_SUM.get(k, 0.0) + value
    _HIST_COUNT[k] = _HIST_COUNT.get(k, 0) + 1


def _fmt_labels(pairs: tuple[tuple[str, str], ...]) -> str:
    if not pairs:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in pairs)
    return "{" + inner + "}"


def render_prom_text() -> str:
    """Serialise the registry in Prometheus text format. Called by /metrics."""
    out: list[str] = []
    for (name, labels), c in sorted(_COUNTERS.items()):
        out.append(f"{name}{_fmt_labels(labels)} {c.value}")
    for (name, labels), g in sorted(_GAUGES.items()):
        out.append(f"{name}{_fmt_labels(labels)} {g.value}")
    for (name, labels), s in sorted(_HIST_SUM.items()):
        out.append(f"{name}_sum{_fmt_labels(labels)} {s}")
        out.append(f"{name}_count{_fmt_labels(labels)} {_HIST_COUNT.get((name, labels), 0)}")
    return "\n".join(out) + ("\n" if out else "")
