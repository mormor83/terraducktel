"""Unit tests for the live-state inventory + diff (pure, no I/O)."""
import json

import pytest

from app.services.envdiff.state import (
    REDACTED,
    Inventory,
    normalizer,
    parse_state,
    state_diff,
)

DEV = "333333333333"
PROD = "444444444444"

SECRETS = ("hunter2-db-pass", "tok-abc123-secret", "-----BEGIN RSA PRIVATE KEY-----xyz", "nested-sens-0")


def _state(resources, serial=7, lineage="lin-1"):
    return {
        "version": 4,
        "terraform_version": "1.10.5",
        "serial": serial,
        "lineage": lineage,
        "outputs": {},
        "resources": resources,
    }


def _bucket(acct, region="us-east-1", name="logs", module=None, versioning=True):
    r = {
        "mode": "managed",
        "type": "aws_s3_bucket",
        "name": name,
        "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
        "instances": [{
            "schema_version": 0,
            "attributes": {
                "id": f"acme-{acct}-{name}",
                "arn": f"arn:aws:s3:::acme-{acct}-{name}",
                "bucket": f"acme-{acct}-{name}",
                "region": region,
                "policy": f'{{"Principal":{{"AWS":"arn:aws:iam::{acct}:root"}}}}',
                "tags": {"team": "platform", "kubernetes.io/role": "elb"},
                "tags_all": {"team": "platform", "env": "x"},
                "versioning": [{"enabled": versioning, "mfa_delete": False}],
                "creation_date": "2026-01-01T00:00:00Z",
            },
            "sensitive_attributes": [],
        }],
    }
    if module:
        r["module"] = module
    return r


def _db(acct, instance_class="db.t3.small"):
    return {
        "mode": "managed",
        "type": "aws_db_instance",
        "name": "main",
        "module": "module.data.module.rds",
        "instances": [{
            "index_key": 0,
            "attributes": {
                "id": "db-1",
                "identifier": "main",
                "instance_class": instance_class,
                "engine_version": "16.3",
                "master_password": "hunter2-db-pass",
                "kms_key_id": f"arn:aws:kms:us-east-1:{acct}:key/abc",
                "created_at": "2026-01-01",
                "extra": {"list": [{"value": "nested-sens-0"}, {"value": "fine"}]},
            },
            # nested get_attr + numeric index path
            "sensitive_attributes": [[
                {"type": "get_attr", "value": "extra"},
                {"type": "get_attr", "value": "list"},
                {"type": "index", "value": {"value": 0, "type": "number"}},
            ]],
        }],
    }


def _cache():
    return {
        "mode": "managed",
        "type": "aws_elasticache_replication_group",
        "name": "redis",
        "instances": [
            {"index_key": "a", "attributes": {"auth_token": "tok-abc123-secret", "node_type": "cache.t3.micro"}},
            {"index_key": "b", "attributes": {
                "node_type": "cache.t3.micro",
                "connection": [{"host": "h", "private_key": "-----BEGIN RSA PRIVATE KEY-----xyz"}],
            }},
        ],
    }


def _data_source():
    return {
        "mode": "data",
        "type": "aws_caller_identity",
        "name": "current",
        "instances": [{"attributes": {"account_id": DEV}}],
    }


def _dev_state():
    return _state([_bucket(DEV), _db(DEV), _cache(), _data_source(),
                   _bucket(DEV, name="only-dev", module="module.extra[0]")])


def _prod_state():
    return _state([_bucket(PROD), _db(PROD, instance_class="db.r6g.large"), _cache(),
                   _bucket(PROD, name="only-prod")], serial=42, lineage="lin-2")


class TestParse:
    def test_addresses_modules_and_indexes(self):
        inv = parse_state(_dev_state())
        assert set(inv.resources) == {
            "aws_s3_bucket.logs",
            "module.data.module.rds.aws_db_instance.main[0]",
            'aws_elasticache_replication_group.redis["a"]',
            'aws_elasticache_replication_group.redis["b"]',
            "module.extra[0].aws_s3_bucket.only-dev",
        }
        db = inv.resources["module.data.module.rds.aws_db_instance.main[0]"]
        assert db.module == "module.data.module.rds"
        assert db.type == "aws_db_instance"

    def test_data_sources_ignored(self):
        inv = parse_state(_dev_state())
        assert not any("aws_caller_identity" in a for a in inv.resources)

    def test_meta(self):
        inv = parse_state(_dev_state())
        assert inv.meta.serial == 7
        assert inv.meta.lineage == "lin-1"
        assert inv.meta.terraform_version == "1.10.5"
        assert inv.meta.resource_count == 5

    def test_flattening(self):
        attrs = parse_state(_dev_state()).resources["aws_s3_bucket.logs"].attributes
        assert attrs["tags.team"] == "platform"
        assert attrs['tags["kubernetes.io/role"]'] == "elb"
        assert attrs["versioning[0].enabled"] == "true"
        assert attrs["versioning[0].mfa_delete"] == "false"
        assert attrs["bucket"] == f"acme-{DEV}-logs"

    def test_noise_keys_dropped(self):
        attrs = parse_state(_dev_state()).resources["aws_s3_bucket.logs"].attributes
        for k in attrs:
            assert not k.startswith(("id", "arn", "tags_all", "creation_date")), k
        db = parse_state(_dev_state()).resources["module.data.module.rds.aws_db_instance.main[0]"].attributes
        assert "created_at" not in db
        # meaningful config is kept
        assert db["instance_class"] == "db.t3.small"
        assert db["engine_version"] == "16.3"
        assert db["identifier"] == "main"

    def test_sensitive_attributes_redacted(self):
        db = parse_state(_dev_state()).resources["module.data.module.rds.aws_db_instance.main[0]"].attributes
        assert db["extra.list[0]"] == REDACTED
        assert db["extra.list[1].value"] == "fine"

    def test_secret_globs_redacted(self):
        inv = parse_state(_dev_state())
        db = inv.resources["module.data.module.rds.aws_db_instance.main[0]"].attributes
        assert db["master_password"] == REDACTED
        a = inv.resources['aws_elasticache_replication_group.redis["a"]'].attributes
        assert a["auth_token"] == REDACTED
        b = inv.resources['aws_elasticache_replication_group.redis["b"]'].attributes
        assert b["connection[0].private_key"] == REDACTED
        assert b["connection[0].host"] == "h"

    def test_no_secret_anywhere_in_inventory(self):
        inv = parse_state(_dev_state())
        blob = json.dumps({a: r.attributes for a, r in inv.resources.items()})
        for s in SECRETS:
            assert s not in blob

    @pytest.mark.parametrize("raw", [None, "", b"", "   ", {}])
    def test_empty_state(self, raw):
        inv = parse_state(raw)
        assert inv.resources == {}
        assert inv.meta.resource_count == 0
        assert inv.meta.serial is None

    def test_accepts_bytes_and_str(self):
        raw = json.dumps(_dev_state())
        assert set(parse_state(raw).resources) == set(parse_state(raw.encode()).resources)

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_state("{not json")


class TestDiff:
    def _diff(self, **kw):
        return state_diff(parse_state(_dev_state()), parse_state(_prod_state()), **kw)

    def test_type_counts(self):
        d = self._diff()
        assert d["type_counts"] == [
            {"type": "aws_db_instance", "source": 1, "target": 1},
            {"type": "aws_elasticache_replication_group", "source": 2, "target": 2},
            {"type": "aws_s3_bucket", "source": 2, "target": 2},
        ]

    def test_only_in_lists(self):
        d = self._diff()
        assert d["only_in_source"] == ["module.extra[0].aws_s3_bucket.only-dev"]
        assert d["only_in_target"] == ["aws_s3_bucket.only-prod"]
        assert d["truncated"] is False

    def test_without_normalize_account_ids_differ(self):
        d = self._diff()
        diff = {r["address"]: r for r in d["differing"]}
        keys = {a["key"] for a in diff["aws_s3_bucket.logs"]["attributes"]}
        assert keys == {"bucket", "policy"}

    def test_normalize_makes_account_only_differences_vanish(self):
        d = self._diff(normalize=normalizer([(DEV, PROD)]))
        diff = {r["address"]: r for r in d["differing"]}
        assert "aws_s3_bucket.logs" not in diff
        # the real difference survives, and it's the only one on the db
        db = diff["module.data.module.rds.aws_db_instance.main[0]"]
        assert db["attributes"] == [
            {"key": "instance_class", "source": "db.t3.small", "target": "db.r6g.large"}
        ]
        assert d["in_both"] == 4
        assert d["identical"] == 3

    def test_redacted_vs_redacted_is_not_a_difference(self):
        d = self._diff(normalize=normalizer([(DEV, PROD)]))
        for r in d["differing"]:
            for a in r["attributes"]:
                assert not (a["source"] == REDACTED and a["target"] == REDACTED)

    def test_one_sided_key_counts_with_none(self):
        src = parse_state(_state([{"mode": "managed", "type": "t", "name": "x",
                                   "instances": [{"attributes": {"a": "1", "b": "2"}}]}]))
        tgt = parse_state(_state([{"mode": "managed", "type": "t", "name": "x",
                                   "instances": [{"attributes": {"a": "1"}}]}]))
        d = state_diff(src, tgt)
        assert d["differing"][0]["attributes"] == [{"key": "b", "source": "2", "target": None}]

    def test_region_rewrite_via_normalizer(self):
        src = parse_state(_state([_bucket(DEV, region="us-east-1")]))
        tgt = parse_state(_state([_bucket(PROD, region="eu-west-1")]))
        d = state_diff(src, tgt, normalize=normalizer([(DEV, PROD), ("us-east-1", "eu-west-1")]))
        assert d["differing"] == []
        assert d["identical"] == 1

    def test_no_secrets_leak_into_diff(self):
        # Target holds DIFFERENT secret values; still nothing leaks, from either side.
        prod = _prod_state()
        prod["resources"][1]["instances"][0]["attributes"]["master_password"] = "prod-pass-zzz"
        d = state_diff(parse_state(_dev_state()), parse_state(prod))
        blob = json.dumps(d)
        for s in SECRETS + ("prod-pass-zzz",):
            assert s not in blob

    def test_meta_in_result(self):
        d = self._diff()
        assert d["source_meta"]["serial"] == 7
        assert d["target_meta"]["lineage"] == "lin-2"

    def test_caps_and_truncation_flags(self):
        many = [{"mode": "managed", "type": "t", "name": f"r{i}", "instances": [{"attributes": {"v": "1"}}]}
                for i in range(12)]
        d = state_diff(parse_state(_state(many)), Inventory(), max_resources=5)
        assert len(d["only_in_source"]) == 5
        assert d["truncated"] is True

        wide = {f"k{i}": str(i) for i in range(10)}
        src = parse_state(_state([{"mode": "managed", "type": "t", "name": "x", "instances": [{"attributes": wide}]}]))
        tgt = parse_state(_state([{"mode": "managed", "type": "t", "name": "x", "instances": [{"attributes": {}}]}]))
        d = state_diff(src, tgt, max_attrs=3)
        assert len(d["differing"][0]["attributes"]) == 3
        assert d["differing"][0]["attrs_truncated"] is True

    def test_diff_is_json_serialisable_and_empty_ok(self):
        d = state_diff(parse_state(None), parse_state(None))
        json.dumps(d)
        assert d["type_counts"] == [] and d["in_both"] == 0 and d["identical"] == 0


def test_normalizer_is_ordered():
    n = normalizer([("a", "b"), ("b", "c")])
    assert n("a-b") == "c-c"
