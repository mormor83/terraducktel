"""Unit tests for the Slack changelog/notification leaf-path helper.

`_leaf_path` strips the `account-<id>/<region>/` prefix from a workspace's
`tf_working_dir` so Slack alerts show a readable path (e.g. `relprod/ai-cog`)
instead of the full repo path. Mirrors the UI's `workspacePathSegments`.
"""
from app.services.notification_service import _leaf_path


def test_leaf_path_strips_account_and_region():
    assert (
        _leaf_path("account-222222222222/us-east-1/relprod/ai-cog", "us-east-1")
        == "relprod/ai-cog"
    )


def test_leaf_path_strips_account_only_when_region_mismatches():
    # Region arg doesn't match the path's region segment → only account stripped.
    assert (
        _leaf_path("account-123/eu-west-1/cust01/ms-worker", "us-east-1")
        == "eu-west-1/cust01/ms-worker"
    )


def test_leaf_path_keeps_non_aws_paths():
    assert _leaf_path("charts/demo", None) == "charts/demo"


def test_leaf_path_empty_for_root_or_unset():
    assert _leaf_path(".", "us-east-1") == ""
    assert _leaf_path("", "us-east-1") == ""
    assert _leaf_path(None, None) == ""
