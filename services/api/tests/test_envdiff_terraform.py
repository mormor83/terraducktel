"""Tests for the terraform config diff engine (pure — no DB, no git)."""
import hcl2
import pytest
import yaml

from app.services.envdiff import get_engine
from app.services.envdiff.terraform import TerraformEngine, normalize

DEV = "333333333333"
PROD = "444444444444"

RULES = {
    "keys": ["*account_id*", "*arn*", "env", "instance_type", "*_count", "*kms*"],
    "values": [DEV, PROD, f"arn:*:{DEV}:*", f"arn:*:{PROD}:*"],
}

SRC_MAIN = f'''terraform {{
  required_version = ">= 1.10"
  backend "s3" {{
    bucket = "tf-state-{DEV}"
    key    = "monitoring/stack/terraform.tfstate"
  }}
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }}
  }}
}}

provider "aws" {{
  region = "us-east-1"
}}

module "mon" {{
  source        = "git::https://github.com/acme/mods.git//mon?ref=v1.6.2"
  instance_type = "t3.small"
  retention     = 30
  alarm_topic   = "arn:aws:sns:us-east-1:{DEV}:alerts"
  tags = {{
    team = "sre"
    env  = "dev"
  }}
}}

resource "aws_security_group" "web" {{
  name = "web"
  ingress {{
    from_port = 443
    to_port   = 443
  }}
  ingress {{
    from_port = 8443
    to_port   = 8443
  }}
}}
'''

TGT_MAIN = f'''terraform {{
  required_version = ">= 1.9"
  backend "s3" {{
    bucket = "tf-state-{PROD}"
    key    = "monitoring/stack/terraform.tfstate"
  }}
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }}
  }}
}}

provider "aws" {{
  region = "us-east-1"
}}

module "mon" {{
  source        = "git::https://github.com/acme/mods.git//mon?ref=v1.4.0"
  instance_type = "m5.large" # prod sizing
  retention     = 14
  alarm_topic   = "arn:aws:sns:us-east-1:{PROD}:alerts"
  legacy_flag   = true
  tags = {{
    team = "sre"
    env  = "prod"
  }}
}}

resource "aws_security_group" "web" {{
  name = "web"
  ingress {{
    from_port = 443
    to_port   = 443
  }}
  ingress {{
    from_port = 8080
    to_port   = 8080
  }}
}}
'''

SRC_VARS = '''variable "worker_count" {
  type    = number
  default = 2
}
'''
TGT_VARS = '''variable "worker_count" {
  type    = number
  default = 6
}
'''

SRC_TFVARS = 'log_level = "debug"\nfeature_x = true\n'
TGT_TFVARS = 'log_level = "warn"\n'

SRC_LOCK = '''provider "registry.terraform.io/hashicorp/aws" {
  version     = "5.82.0"
  constraints = "~> 5.80"
}
'''
TGT_LOCK = '''provider "registry.terraform.io/hashicorp/aws" {
  version     = "5.61.0"
  constraints = "~> 5.60"
}
'''

SRC_YAML = "terraform:\n  version: \"1.10.5\"  # pinned\ncheckov:\n  skip: []\n"
TGT_YAML = "terraform:\n  version: \"1.9.8\"  # pinned\ncheckov:\n  skip: []\n"


def leaf(main=SRC_MAIN, vars_=SRC_VARS, tfvars=SRC_TFVARS, lock=SRC_LOCK, yml=SRC_YAML, **extra):
    files = {
        "main.tf": main,
        "variables.tf": vars_,
        "terraform.tfvars": tfvars,
        ".terraform.lock.hcl": lock,
        "terraducktel.yaml": yml,
    }
    files.update(extra)
    return {k: v for k, v in files.items() if v is not None}


@pytest.fixture
def engine():
    return TerraformEngine()


def by_key(diff, key, file=None):
    hits = [h for h in diff.hunks if h.key == key and (file is None or h.file == file)]
    assert hits, f"no hunk for {key}: {[h.key for h in diff.hunks]}"
    return hits[0]


class TestRealisticLeaf:
    @pytest.fixture
    def diff(self, engine):
        return engine.config_diff(
            leaf(), leaf(TGT_MAIN, TGT_VARS, TGT_TFVARS, TGT_LOCK, TGT_YAML), RULES
        )

    def test_module_version_bump_from_source_ref(self, diff):
        h = by_key(diff, "module.mon.source")
        assert h.category == "module" and h.kind == "changed"
        assert {"key": "module.mon", "from": "v1.4.0", "to": "v1.6.2"} in diff.summary["module_versions"]

    def test_backend_is_never_promotable(self, diff):
        h = by_key(diff, "terraform.backend.s3.bucket")
        assert h.classification == "backend"
        assert not h.applicable
        assert "never promoted" in h.not_applicable_reason
        assert diff.summary["backend_count"] == 1

    def test_protected_by_key_and_value(self, diff):
        it = by_key(diff, "module.mon.instance_type")
        assert it.classification == "protected" and "instance_type" in it.protected_reason
        topic = by_key(diff, "module.mon.alarm_topic")
        assert topic.classification == "protected"
        assert DEV in topic.protected_reason or "arn" in topic.protected_reason
        env = by_key(diff, "module.mon.tags.env")
        assert env.classification == "protected"
        count = by_key(diff, "variable.worker_count.default")
        assert count.classification == "protected"  # *_count matches the block label segment
        assert count.category == "inputs"

    def test_plain_inputs_are_promotable(self, diff):
        h = by_key(diff, "module.mon.retention")
        assert (h.classification, h.category, h.source_value, h.target_value) == (
            "promotable", "inputs", "30", "14")

    def test_repeated_nested_blocks_are_indexed(self, diff):
        h = by_key(diff, "resource.aws_security_group.web.ingress[1].from_port")
        assert (h.source_value, h.target_value) == ("8443", "8080")
        assert not [x for x in diff.hunks if x.key.startswith("resource.aws_security_group.web.ingress[0]")]

    def test_providers_and_lock(self, diff):
        rv = by_key(diff, "terraform.required_version")
        assert rv.category == "providers"
        lock = by_key(diff, 'provider."registry.terraform.io/hashicorp/aws".version', ".terraform.lock.hcl")
        assert lock.category == "providers"
        assert lock.source_value == '"5.82.0"'
        keys = {p["key"] for p in diff.summary["providers"]}
        assert "terraform.required_providers.aws.version" in keys

    def test_removed_key(self, diff):
        h = by_key(diff, "module.mon.legacy_flag")
        assert h.kind == "removed" and h.applicable and h.source_value is None

    def test_tfvars(self, diff):
        assert by_key(diff, "log_level", "terraform.tfvars").category == "inputs"
        added = by_key(diff, "feature_x", "terraform.tfvars")
        assert added.kind == "added" and added.applicable

    def test_yaml_scalar(self, diff):
        h = by_key(diff, "terraform.version", "terraducktel.yaml")
        assert h.category == "module" and h.applicable
        assert h.source_value == '"1.10.5"'

    def test_line_ranges(self, diff):
        h = by_key(diff, "module.mon.retention")
        assert h.source_lines == (22, 22) and h.target_lines == (22, 22)

    def test_summary_counts(self, diff):
        s = diff.summary
        assert s["files_changed"] == 5
        assert s["keys_changed"] == len(diff.hunks)
        assert s["protected_count"] + s["backend_count"] + s["promotable_count"] == len(diff.hunks)
        assert sum(s["categories"].values()) == len(diff.hunks)

    def test_to_dict_is_json_ready(self, diff):
        import json
        json.dumps(diff.to_dict())

    def test_apply_all_then_rediff_converges(self, engine):
        src = leaf()
        tgt = leaf(TGT_MAIN, TGT_VARS, TGT_TFVARS, TGT_LOCK, TGT_YAML)
        d = engine.config_diff(src, tgt, RULES)
        ids = [h.id for h in d.hunks if h.applicable]
        changed, errors = engine.apply_hunks(src, tgt, ids, RULES)
        assert errors == []
        new_tgt = {**tgt, **{k: v for k, v in changed.items() if v is not None}}
        for k, v in changed.items():
            if v is None:
                new_tgt.pop(k)
        again = engine.config_diff(src, new_tgt, RULES)
        assert all(not h.applicable for h in again.hunks), [(h.key, h.kind) for h in again.hunks if h.applicable]
        assert [h.key for h in again.hunks] == ["terraform.backend.s3.bucket"]
        # Comments and formatting outside the edited spans survive.
        assert "# prod sizing" in new_tgt["main.tf"]
        assert "# pinned" in new_tgt["terraducktel.yaml"]


class TestNormalisation:
    def test_formatting_comments_and_order_produce_no_hunks(self, engine):
        a = 'module "m" {\n  a = 1\n  b = [1, 2, 3]\n  c = { x = "y" }\n}\n'
        b = ('# header\nmodule "m" {\n  c = {\n    x = "y" # why\n  }\n  b = [\n    1,\n    2,\n    3,\n  ]\n'
             '  /* block */ a    =    1\n}\n')
        d = engine.config_diff({"main.tf": a}, {"main.tf": b}, RULES)
        assert d.hunks == []

    def test_normalize_keeps_string_whitespace(self):
        assert normalize('"a  b"') != normalize('"a b"')
        assert normalize("[ 1 ,2, ]") == normalize("[1, 2]")


class TestApply:
    def _apply(self, engine, src, tgt, pick=lambda h: True):
        d = engine.config_diff({"main.tf": src}, {"main.tf": tgt}, RULES)
        ids = [h.id for h in d.hunks if pick(h)]
        changed, errors = engine.apply_hunks({"main.tf": src}, {"main.tf": tgt}, ids, RULES)
        return d, changed, errors

    def test_changed_value_is_spliced(self, engine):
        src = 'module "m" {\n  retention = 30\n}\n'
        tgt = 'module "m" {\n  retention   = 14   # keep me\n}\n'
        _, changed, errors = self._apply(engine, src, tgt)
        assert errors == []
        assert changed["main.tf"] == 'module "m" {\n  retention   = 30   # keep me\n}\n'

    def test_added_key_goes_in_existing_block_with_sibling_indent(self, engine):
        src = 'module "m" {\n    a = 1\n    b = 2\n}\n'
        tgt = 'module "m" {\n    a = 1\n}\n'
        d, changed, errors = self._apply(engine, src, tgt)
        assert [h.kind for h in d.hunks] == ["added"]
        assert errors == []
        assert changed["main.tf"] == 'module "m" {\n    a = 1\n    b = 2\n}\n'

    def test_added_key_into_empty_block(self, engine):
        src = 'resource "x" "y" {\n  a = 1\n}\n'
        tgt = 'resource "x" "y" {\n}\n'
        _, changed, errors = self._apply(engine, src, tgt)
        assert errors == []
        assert changed["main.tf"] == 'resource "x" "y" {\n  a = 1\n}\n'

    def test_added_nested_object_is_grouped_and_inserted_once(self, engine):
        src = 'module "m" {\n  a = 1\n  tags = {\n    team = "sre"\n    tier = "web"\n  }\n}\n'
        tgt = 'module "m" {\n  a = 1\n}\n'
        d, changed, errors = self._apply(engine, src, tgt)
        assert {h.group for h in d.hunks} == {"module.m.tags"}
        assert len(d.hunks) == 2
        assert errors == []
        assert changed["main.tf"] == src
        # Selecting just one leaf of the group still brings the whole object.
        _, changed_one, _ = self._apply(engine, src, tgt, pick=lambda h: h.key.endswith("team"))
        assert changed_one["main.tf"] == src

    def test_added_missing_top_level_block(self, engine):
        src = 'module "a" {\n  x = 1\n}\n\nmodule "b" {\n  y = 2\n  z = {\n    k = 1\n  }\n}\n'
        tgt = 'module "a" {\n  x = 1\n}\n'
        _, changed, errors = self._apply(engine, src, tgt)
        assert errors == []
        assert changed["main.tf"] == src
        hcl2.parses_to_tree(changed["main.tf"])

    def test_removed_key_deletes_its_lines(self, engine):
        src = 'module "m" {\n  a = 1\n}\n'
        tgt = 'module "m" {\n  a = 1\n  b = [\n    1,\n    2,\n  ] # old\n}\n'
        _, changed, errors = self._apply(engine, src, tgt)
        assert errors == []
        assert changed["main.tf"] == src

    def test_removed_block_is_one_group(self, engine):
        src = 'module "m" {\n  a = 1\n}\n'
        tgt = 'module "m" {\n  a = 1\n  lifecycle {\n    x = 1\n    y = 2\n  }\n}\n'
        d, changed, errors = self._apply(engine, src, tgt)
        assert {h.group for h in d.hunks} == {"module.m.lifecycle"}
        assert changed["main.tf"] == src

    def test_removed_key_sharing_a_line_is_not_applicable(self, engine):
        src = 'module "m" {\n  o = { a = 1 }\n}\n'
        tgt = 'module "m" {\n  o = { a = 1, b = 2 }\n}\n'
        d = engine.config_diff({"main.tf": src}, {"main.tf": tgt}, RULES)
        h = by_key(d, "module.m.o.b")
        assert not h.applicable and "line" in h.not_applicable_reason

    def test_added_into_single_line_block_is_not_applicable(self, engine):
        src = 'module "m" { a = 1\n b = 2 }\n'
        tgt = 'module "m" { a = 1 }\n'
        d = engine.config_diff({"main.tf": src}, {"main.tf": tgt}, RULES)
        assert not by_key(d, "module.m.b").applicable

    def test_heredoc_and_list_changes(self, engine):
        src = 'resource "r" "x" {\n  policy = <<EOT\n{"a": 1}\nEOT\n  ports = [80, 443]\n}\n'
        tgt = 'resource "r" "x" {\n  policy = <<EOT\n{"a": 2}\nEOT\n  ports = [80]\n}\n'
        d, changed, errors = self._apply(engine, src, tgt)
        assert {h.key for h in d.hunks} == {"resource.r.x.policy", "resource.r.x.ports"}
        assert errors == []
        assert changed["main.tf"] == src

    def test_leaf_vs_object_mismatch(self, engine):
        src = 'module "m" {\n  tags = { a = 1 }\n}\n'
        tgt = 'module "m" {\n  tags = var.tags\n}\n'
        d, changed, errors = self._apply(engine, src, tgt)
        assert [(h.key, h.kind) for h in d.hunks] == [("module.m.tags", "changed")]
        assert changed["main.tf"] == src

    def test_hunk_ids_are_stable_across_calls_and_formatting(self, engine):
        src = 'module "m" {\n  a = 1\n}\n'
        tgt = 'module "m" {\n  a = 2\n}\n'
        tgt_reformatted = '# note\nmodule "m" {\n    a     =    2\n}\n'
        i1 = engine.config_diff({"main.tf": src}, {"main.tf": tgt}, RULES).hunks[0].id
        i2 = engine.config_diff({"main.tf": src}, {"main.tf": tgt}, RULES).hunks[0].id
        i3 = engine.config_diff({"main.tf": src}, {"main.tf": tgt_reformatted}, RULES).hunks[0].id
        assert i1 == i2 == i3

    def test_rejects_unknown_backend_and_not_applicable(self, engine):
        src = leaf()
        tgt = leaf(TGT_MAIN, TGT_VARS, TGT_TFVARS, TGT_LOCK, TGT_YAML)
        d = engine.config_diff(src, tgt, RULES)
        backend = by_key(d, "terraform.backend.s3.bucket")
        changed, errors = engine.apply_hunks(src, tgt, ["deadbeefdeadbeef", backend.id], RULES)
        assert changed == {}
        assert len(errors) == 2
        assert "unknown hunk" in errors[0] and "never promoted" in errors[1]

    def test_protected_hunk_can_be_applied_when_selected(self, engine):
        src = 'module "m" {\n  instance_type = "t3.small"\n}\n'
        tgt = 'module "m" {\n  instance_type = "m5.large"\n}\n'
        d, changed, errors = self._apply(engine, src, tgt)
        assert d.hunks[0].classification == "protected"
        assert changed["main.tf"] == src and errors == []


class TestFiles:
    def test_ignored_files(self, engine):
        src = {"main.tf": "a = 1\n", ".terraform/modules/x.tf": "x", "terraform.tfstate": "{}",
               "_terraducktel_backend.tf": "terraform {}", "tfplan.bin": "b"}
        d = engine.config_diff(src, {"main.tf": "a = 1\n"}, RULES)
        assert d.hunks == [] and [f.path for f in d.files] == ["main.tf"]

    def test_file_level_add_remove_replace(self, engine):
        src = {"main.tf": "a = 1\n", "outputs.tf": 'output "o" {\n  value = 1\n}\n', "policy.json": '{"v": 2}'}
        tgt = {"main.tf": "a = 1\n", "old.tf": "b = 1\n", "policy.json": '{"v": 1}'}
        d = engine.config_diff(src, tgt, RULES)
        kinds = {(h.file, h.kind, h.level) for h in d.hunks}
        assert kinds == {("outputs.tf", "added", "file"), ("old.tf", "removed", "file"),
                         ("policy.json", "changed", "file")}
        changed, errors = engine.apply_hunks(src, tgt, [h.id for h in d.hunks], RULES)
        assert errors == []
        assert changed == {"outputs.tf": src["outputs.tf"], "old.tf": None, "policy.json": '{"v": 2}'}
        assert {f.path: f.status for f in d.files} == {
            "main.tf": "unchanged", "old.tf": "removed", "outputs.tf": "added", "policy.json": "modified"}

    def test_file_level_protected_by_account_in_content(self, engine):
        d = engine.config_diff({"x.json": f'{{"acct": "{DEV}"}}'}, {}, RULES)
        assert d.hunks[0].classification == "protected"

    def test_backend_tf_file_is_backend(self, engine):
        d = engine.config_diff({"backend.tf": 'terraform {\n  backend "s3" {}\n}\n'}, {}, RULES)
        assert d.hunks[0].classification == "backend" and not d.hunks[0].applicable

    def test_unparseable_hcl_becomes_whole_file_change(self, engine):
        src = {"main.tf": "module \"m\" {\n  a = \n"}
        tgt = {"main.tf": 'module "m" {\n  a = 1\n}\n'}
        d = engine.config_diff(src, tgt, RULES)
        assert d.warnings and "could not parse" in d.warnings[0]
        assert d.hunks[0].level == "file" and d.hunks[0].applicable

    def test_unified_diff_and_truncation(self, engine):
        d = engine.config_diff({"main.tf": "a = 1\n"}, {"main.tf": "a = 2\n"}, RULES)
        f = d.files[0]
        assert f.status == "modified" and "-a = 2" in f.unified and "+a = 1" in f.unified
        big = "x" * 250_000
        d2 = engine.config_diff({"blob.txt": big}, {"blob.txt": "y"}, RULES)
        assert d2.files[0].truncated and len(d2.files[0].source_text) == 200_000

    def test_result_that_fails_to_parse_is_not_returned(self, engine, monkeypatch):
        src, tgt = {"main.tf": "a = 1\n"}, {"main.tf": "a = 2\n"}
        d = engine.config_diff(src, tgt, RULES)
        monkeypatch.setattr(TerraformEngine, "_verify", staticmethod(lambda p, t: "Boom"))
        changed, errors = engine.apply_hunks(src, tgt, [d.hunks[0].id], RULES)
        assert changed == {} and "does not parse" in errors[0]


class TestYaml:
    def test_non_scalar_and_added_not_applicable(self, engine):
        src = {"terraducktel.yaml": "checkov:\n  skip: [CKV_1]\nnew_key: 1\n"}
        tgt = {"terraducktel.yaml": "checkov:\n  skip: []\n"}
        d = engine.config_diff(src, tgt, RULES)
        assert {(h.key, h.applicable) for h in d.hunks} == {("checkov.skip", False), ("new_key", False)}

    def test_scalar_change_applied_and_valid(self, engine):
        src = {"terraducktel.yaml": "terraform:\n  version: 1.10.5\n"}
        tgt = {"terraducktel.yaml": "# cfg\nterraform:\n  version: 1.9.8 # old\n"}
        d = engine.config_diff(src, tgt, RULES)
        changed, errors = engine.apply_hunks(src, tgt, [d.hunks[0].id], RULES)
        assert errors == []
        assert changed["terraducktel.yaml"] == "# cfg\nterraform:\n  version: 1.10.5 # old\n"
        assert yaml.safe_load(changed["terraducktel.yaml"]) == {"terraform": {"version": "1.10.5"}}


class TestEngines:
    def test_get_engine(self):
        assert get_engine("terraform").name == "terraform"
        helm = get_engine("helm")
        with pytest.raises(NotImplementedError, match="coming soon"):
            helm.config_diff({}, {}, {})
        with pytest.raises(ValueError):
            get_engine("pulumi")
