"""Phase 1 critical: FSM invariants for Run.transition().

CRITICAL: PLANNED → APPLYING bypasses 4-eyes approval. Only AWAITING_APPROVAL
→ APPLYING is permitted, so the approval router is the single gateway.
"""
import pytest

from app.models.run import Run, RunStatus


def test_planned_cannot_skip_to_applying():
    """PLANNED → APPLYING must raise ValueError ('Invalid transition')."""
    run = Run(workspace_id="ws-1", command="apply", status=RunStatus.PLANNED)
    with pytest.raises(ValueError, match="Invalid transition"):
        run.transition(RunStatus.APPLYING)


def test_planned_must_go_through_awaiting_approval():
    """The only legal path from PLANNED to APPLYING is via AWAITING_APPROVAL."""
    run = Run(workspace_id="ws-1", command="apply", status=RunStatus.PLANNED)

    # Step 1: PLANNED → AWAITING_APPROVAL (allowed)
    run.transition(RunStatus.AWAITING_APPROVAL)
    assert run.status == RunStatus.AWAITING_APPROVAL

    # Step 2: AWAITING_APPROVAL → APPLYING (allowed)
    run.transition(RunStatus.APPLYING)
    assert run.status == RunStatus.APPLYING
