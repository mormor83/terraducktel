"""The `since` cursor in the watch loop.

The cursor must never run ahead of a step that hasn't finished, or the watcher
would silently stop reporting it — which on a long apply is the difference
between "Terraform Apply failed" and apparent silence.
"""
import pytest

from tdt.commands.run_cmd import _StepStreamer


class FakeClient:
    """Records the `since` values asked for and replays scripted step lists."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.since_asked: list[int] = []

    def get(self, path, params=None):
        self.since_asked.append((params or {}).get("since"))
        return self._frames.pop(0) if self._frames else []


def steps(*specs):
    return [{"position": p, "name": n, "status": s, "duration_seconds": 1} for p, n, s in specs]


def test_cursor_advances_past_the_finished_prefix():
    frames = [
        steps((0, "Clone", "success"), (1, "Init", "running"), (2, "Plan", "pending")),
        steps((1, "Init", "success"), (2, "Plan", "running")),
    ]
    client = FakeClient(frames)
    s = _StepStreamer(client, "run1", quiet=True)

    s.poll()
    assert client.since_asked == [0]
    assert s._cursor == 1, "only step 0 was terminal"

    s.poll()
    assert client.since_asked == [0, 1]
    assert s._cursor == 2


def test_cursor_does_not_skip_a_running_step_with_a_finished_successor():
    """Step 1 running while step 2 already succeeded must NOT advance past 1."""
    client = FakeClient([
        steps((0, "Clone", "success"), (1, "Init", "running"), (2, "Plan", "success")),
    ])
    s = _StepStreamer(client, "run1", quiet=True)
    s.poll()
    assert s._cursor == 1, "cursor must stop at the first non-terminal step"


def test_a_step_is_announced_exactly_once():
    """Re-polling the same terminal step must not reprint it."""
    same = steps((0, "Clone", "success"), (1, "Init", "running"))
    client = FakeClient([same, list(same), list(same)])
    s = _StepStreamer(client, "run1", quiet=True)
    for _ in range(3):
        s.poll()
    assert s._announced == {0}


def test_skipped_counts_as_terminal():
    """Cost Estimation is skipped when no Infracost token is set — not a stall."""
    client = FakeClient([steps((0, "Clone", "success"), (1, "Cost", "skipped"), (2, "Plan", "running"))])
    s = _StepStreamer(client, "run1", quiet=True)
    s.poll()
    assert s._cursor == 2


def test_failed_counts_as_terminal_so_the_loop_can_move_on():
    client = FakeClient([steps((0, "Clone", "success"), (1, "Plan", "failed"))])
    s = _StepStreamer(client, "run1", quiet=True)
    s.poll()
    assert s._cursor == 2
    assert s._announced == {0, 1}


def test_empty_step_list_is_survivable():
    client = FakeClient([[]])
    s = _StepStreamer(client, "run1", quiet=True)
    assert s.poll() == []
    assert s._cursor == 0


def test_polling_requests_no_output():
    """The whole point: the timeline poll must not drag the log blobs along."""
    captured = {}

    class Recorder(FakeClient):
        def get(self, path, params=None):
            captured.update(params or {})
            return []

    _StepStreamer(Recorder([]), "run1", quiet=True).poll()
    assert captured["include_output"] == "false"


def test_failed_step_lookup_asks_for_output():
    captured = {}

    class Recorder:
        def get(self, path, params=None):
            captured.update(params or {})
            return steps((0, "Clone", "success"), (1, "Plan", "failed"))

    s = _StepStreamer(Recorder(), "run1", quiet=True)
    failed = s.failed_step()
    assert failed["name"] == "Plan"
    assert captured["include_output"] == "true"


def test_failed_step_lookup_returns_none_when_nothing_failed():
    class Recorder:
        def get(self, path, params=None):
            return steps((0, "Clone", "success"))

    assert _StepStreamer(Recorder(), "run1", quiet=True).failed_step() is None
