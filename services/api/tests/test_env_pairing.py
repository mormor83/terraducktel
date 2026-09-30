"""Unit tests for the environment-link pairing engine (pure, no DB)."""
import pytest

from app.services import env_pairing as ep
from app.services.env_pairing import Stack

DEV = "account-333333333333"
PROD = "account-444444444444"


def _stacks(*paths, kind="terraform"):
    return [Stack(id=p, tf_working_dir=p, kind=kind) for p in paths]


def _by_status(result):
    out = {}
    for p in result.pairs:
        out.setdefault(p.status, []).append(p)
    return out


class TestLevels:
    def test_infer_levels(self):
        stacks = _stacks(f"{DEV}/us-east-1/monitoring/stack")
        assert ep.infer_level(DEV, stacks) == "account"
        assert ep.infer_level(f"{DEV}/us-east-1", stacks) == "region"
        assert ep.infer_level(f"{DEV}/us-east-1/monitoring", stacks) == "folder"
        assert ep.infer_level(f"{DEV}/us-east-1/monitoring/stack", stacks) == "stack"

    def test_node_account_id(self):
        assert ep.node_account_id(f"{DEV}/us-east-1") == "333333333333"
        assert ep.node_account_id("global/foo") is None

    def test_same_level_required(self):
        stacks = _stacks(f"{DEV}/us-east-1/a/stack", f"{PROD}/us-east-1/a/stack")
        with pytest.raises(ep.PairingError, match="same level"):
            ep.validate_nodes(DEV, f"{PROD}/us-east-1", stacks)
        assert ep.validate_nodes(DEV, PROD, stacks) == "account"

    def test_claimed_level_must_match(self):
        stacks = _stacks(f"{DEV}/us-east-1/a/stack", f"{PROD}/us-east-1/a/stack")
        with pytest.raises(ep.PairingError, match="is a account, not a region"):
            ep.validate_nodes(DEV, PROD, stacks, "region", "region")

    def test_overlapping_nodes_rejected(self):
        stacks = _stacks(f"{DEV}/us-east-1/a/stack")
        with pytest.raises(ep.PairingError, match="overlap"):
            ep.validate_nodes(DEV, f"{DEV}/us-east-1", stacks)

    def test_empty_node_rejected(self):
        stacks = _stacks(f"{DEV}/us-east-1/a/stack")
        with pytest.raises(ep.PairingError, match="target node has no stacks"):
            ep.validate_nodes(DEV, PROD, stacks)

    def test_helm_only_node_rejected(self):
        stacks = _stacks(f"{DEV}/us-east-1/a/stack") + _stacks(f"{PROD}/us-east-1/a/chart", kind="helm")
        with pytest.raises(ep.PairingError, match="Helm"):
            ep.validate_nodes(DEV, PROD, stacks)


class TestPairing:
    def test_account_level_pairs_equal_paths(self):
        stacks = _stacks(
            f"{DEV}/us-east-1/monitoring/stack",
            f"{DEV}/us-east-1/vpc/home",
            f"{PROD}/us-east-1/monitoring/stack",
            f"{PROD}/us-east-1/legacy/thing",
        )
        r = ep.pair_stacks(DEV, PROD, stacks)
        s = _by_status(r)
        assert [(p.source_rel, p.target_rel) for p in s["not_compared"]] == [
            ("us-east-1/monitoring/stack", "us-east-1/monitoring/stack")
        ]
        assert [p.source_rel for p in s["missing_in_target"]] == ["us-east-1/vpc/home"]
        assert s["missing_in_target"][0].proposed_target_rel == "us-east-1/vpc/home"
        assert [p.target_rel for p in s["missing_in_source"]] == ["us-east-1/legacy/thing"]

    def test_stack_level_is_a_single_pair_regardless_of_name(self):
        stacks = _stacks(f"{DEV}/us-east-1/monitoring/stack", f"{PROD}/eu-west-1/observability/main")
        r = ep.pair_stacks(f"{DEV}/us-east-1/monitoring/stack", f"{PROD}/eu-west-1/observability/main", stacks)
        assert len(r.pairs) == 1
        p = r.pairs[0]
        assert (p.source_rel, p.target_rel, p.status) == ("", "", "not_compared")

    def test_folder_level_relative_to_node(self):
        stacks = _stacks(f"{DEV}/us-east-1/monitoring/stack", f"{PROD}/us-east-1/observability/stack")
        r = ep.pair_stacks(f"{DEV}/us-east-1/monitoring", f"{PROD}/us-east-1/observability", stacks)
        assert [(p.source_rel, p.target_rel, p.status) for p in r.pairs] == [
            ("stack", "stack", "not_compared")
        ]

    def test_plain_rewrite_rule(self):
        stacks = _stacks(f"{DEV}/us-east-1/app/stack", f"{PROD}/eu-west-1/app/stack")
        r = ep.pair_stacks(DEV, PROD, stacks, [{"from": "us-east-1", "to": "eu-west-1"}])
        assert [p.status for p in r.pairs] == ["not_compared"]

    def test_regex_rewrite_rule_with_backref(self):
        stacks = _stacks(f"{DEV}/us-east-1/app-dev/stack", f"{PROD}/us-east-1/app-prod/stack")
        rules = [{"from": r"^(.*)-dev/", "to": r"\1-prod/", "regex": True}]
        r = ep.pair_stacks(DEV, PROD, stacks, rules)
        assert [(p.source_rel, p.target_rel) for p in r.pairs] == [
            ("us-east-1/app-dev/stack", "us-east-1/app-prod/stack")
        ]

    def test_rules_apply_in_order(self):
        rules = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
        assert ep.apply_rewrites("a/x", rules) == "c/x"

    def test_explicit_pair_override_and_exclusion(self):
        stacks = _stacks(
            f"{DEV}/us-east-1/old-name/stack",
            f"{DEV}/us-east-1/scratch/stack",
            f"{PROD}/us-east-1/new-name/stack",
            f"{PROD}/us-east-1/prod-only/stack",
        )
        overrides = {
            "pairs": [{"source": "us-east-1/old-name/stack", "target": "us-east-1/new-name/stack"}],
            "exclude": [
                {"side": "source", "path": "us-east-1/scratch/stack"},
                {"side": "target", "path": "us-east-1/prod-only/stack"},
            ],
        }
        r = ep.pair_stacks(DEV, PROD, stacks, [], overrides)
        s = _by_status(r)
        assert len(s["not_compared"]) == 1 and s["not_compared"][0].reason == "override"
        assert len(s["excluded"]) == 2
        assert "missing_in_target" not in s and "missing_in_source" not in s

    def test_override_to_unknown_path_warns(self):
        stacks = _stacks(f"{DEV}/us-east-1/a/stack", f"{PROD}/us-east-1/a/stack")
        r = ep.pair_stacks(DEV, PROD, stacks, [], {"pairs": [{"source": "nope", "target": "us-east-1/a/stack"}]})
        assert any("no source stack" in w for w in r.warnings)
        assert [p.status for p in r.pairs] == ["not_compared"]

    def test_duplicate_paths_are_ambiguous_not_paired(self):
        stacks = [
            Stack(id="a", tf_working_dir=f"{DEV}/us-east-1/app/stack"),
            Stack(id="b", tf_working_dir=f"{DEV}/us-east-1/app/stack"),
            Stack(id="c", tf_working_dir=f"{PROD}/us-east-1/app/stack"),
        ]
        r = ep.pair_stacks(DEV, PROD, stacks)
        assert any("Ambiguous" in w for w in r.warnings)
        assert [p.status for p in r.pairs] == ["missing_in_source"]

    def test_two_sources_rewriting_to_one_target_are_ambiguous(self):
        stacks = _stacks(f"{DEV}/us-east-1/a/stack", f"{DEV}/eu-west-1/a/stack", f"{PROD}/eu-west-1/a/stack")
        r = ep.pair_stacks(DEV, PROD, stacks, [{"from": "us-east-1", "to": "eu-west-1"}])
        s = _by_status(r)
        assert len(s["excluded"]) == 2
        assert all(p.reason == "ambiguous rewrite" for p in s["excluded"])

    def test_helm_stacks_are_counted_not_paired(self):
        stacks = _stacks(f"{DEV}/us-east-1/a/stack", f"{PROD}/us-east-1/a/stack") + _stacks(
            f"{DEV}/us-east-1/charts/x", kind="helm"
        )
        r = ep.pair_stacks(DEV, PROD, stacks)
        assert r.helm_skipped == 1
        assert len(r.pairs) == 1

    def test_stacks_outside_nodes_ignored(self):
        stacks = _stacks(f"{DEV}/us-east-1/a/stack", f"{PROD}/us-east-1/a/stack", "account-111111111111/x/y")
        assert len(ep.pair_stacks(DEV, PROD, stacks).pairs) == 1

    def test_prefix_is_segment_aware(self):
        # account-1 must not swallow account-12's stacks.
        stacks = _stacks("account-1234567/r/a", "account-12345678/r/a", "account-7654321/r/a")
        r = ep.pair_stacks("account-1234567", "account-7654321", stacks)
        assert [p.status for p in r.pairs] == ["not_compared"]

    def test_pair_key_is_stable(self):
        stacks = _stacks(f"{DEV}/us-east-1/a/stack", f"{PROD}/us-east-1/a/stack")
        k1 = ep.pair_stacks(DEV, PROD, stacks).pairs[0].key
        k2 = ep.pair_stacks(DEV, PROD, list(reversed(stacks))).pairs[0].key
        assert k1 == k2 == "us-east-1/a/stack=>us-east-1/a/stack"


class TestRuleValidation:
    def test_bad_regex_rejected(self):
        with pytest.raises(ep.PairingError, match="not a valid regex"):
            ep.validate_rewrite_rules([{"from": "(", "to": "x", "regex": True}])

    def test_bad_backref_rejected(self):
        with pytest.raises(ep.PairingError, match="not a valid regex"):
            ep.validate_rewrite_rules([{"from": "a", "to": r"\2", "regex": True}])

    def test_too_many_rules(self):
        with pytest.raises(ep.PairingError, match="At most"):
            ep.validate_rewrite_rules([{"from": "a", "to": "b"}] * 21)


class TestProtectedRules:
    def test_defaults_seed_both_accounts_and_arns(self):
        rules = ep.default_protected_rules("333333333333", "444444444444")
        assert "333333333333" in rules["values"] and "444444444444" in rules["values"]
        assert "arn:*:333333333333:*" in rules["values"]
        assert "instance_type" in rules["keys"]

    def test_key_matching_per_segment(self):
        rules = ep.default_protected_rules("1", "2")
        assert ep.is_protected_key("instance_type", rules)
        assert ep.is_protected_key("node_groups.default.instance_type", rules)
        assert ep.is_protected_key("worker_count", rules)
        assert ep.is_protected_key("private_subnet_ids", rules)
        assert not ep.is_protected_key("module_version", rules)

    def test_value_matching(self):
        rules = ep.default_protected_rules("333333333333", "444444444444")
        assert ep.is_protected_value("333333333333", rules)
        assert ep.is_protected_value("arn:aws:iam::333333333333:role/x", rules)
        assert not ep.is_protected_value("arn:aws:iam::111111111111:role/x", rules)
