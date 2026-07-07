"""Regression tests for observability helpers (task #11).

Pure-Python; no DB needed. Covers:
  - JSONFormatter emits one JSON line per record with the required fields.
  - counter_inc / gauge_set / histogram_observe accumulate per (name, labels).
  - render_prom_text emits valid Prometheus text format.
"""
from __future__ import annotations

import json
import logging

from app import observability as obs


def _read_record_through_formatter(level=logging.INFO, **extras) -> dict:
    fmt = obs.JSONFormatter()
    record = logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=10,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for k, v in extras.items():
        setattr(record, k, v)
    return json.loads(fmt.format(record))


class TestJSONFormatter:
    def test_required_fields(self):
        d = _read_record_through_formatter()
        for key in ("ts", "level", "logger", "msg"):
            assert key in d, f"missing {key} in JSON log line"
        assert d["msg"] == "hello world", "args should be interpolated"
        assert d["level"] == "INFO"
        assert d["logger"] == "test"
        # ts must be ISO-8601 with millisecond Z suffix
        assert d["ts"].endswith("Z")

    def test_extra_fields_are_passed_through(self):
        d = _read_record_through_formatter(run_id="r1", workspace_id="w1")
        assert d.get("run_id") == "r1"
        assert d.get("workspace_id") == "w1"

    def test_non_serialisable_extras_fall_back_to_repr(self):
        class Unserializable:
            def __repr__(self):
                return "<Unserializable>"

        d = _read_record_through_formatter(weird=Unserializable())
        assert d.get("weird") == "<Unserializable>"


class TestRegistry:
    def setup_method(self):
        # Wipe the module-level registry between tests so accumulator state
        # from one test doesn't leak into another's assertions.
        obs._COUNTERS.clear()
        obs._GAUGES.clear()
        obs._HIST_SUM.clear()
        obs._HIST_COUNT.clear()

    def test_counter_accumulates(self):
        obs.counter_inc("c", {"k": "v"})
        obs.counter_inc("c", {"k": "v"}, 2.5)
        obs.counter_inc("c", {"k": "other"})
        text = obs.render_prom_text()
        assert 'c{k="v"} 3.5' in text
        assert 'c{k="other"} 1.0' in text

    def test_gauge_overwrites(self):
        obs.gauge_set("g", 5.0)
        obs.gauge_set("g", 9.0)
        text = obs.render_prom_text()
        assert "\ng 9.0\n" in "\n" + text + "\n", "gauge should hold latest set() value"

    def test_histogram_sums_and_counts(self):
        obs.histogram_observe("h", 1.0)
        obs.histogram_observe("h", 2.0)
        obs.histogram_observe("h", 4.0)
        text = obs.render_prom_text()
        assert "h_sum 7.0" in text
        assert "h_count 3" in text

    def test_labels_serialise_alphabetically(self):
        # Insertion-order should NOT bleed into output — the registry sorts
        # label pairs so the Prom text is stable across runs.
        obs.counter_inc("c", {"b": "2", "a": "1"})
        text = obs.render_prom_text()
        assert 'c{a="1",b="2"}' in text
