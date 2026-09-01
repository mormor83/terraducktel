"""Every flag the shipped SKILL.md documents must actually exist on the CLI.

The skill shipped documenting `tdt run steps <run-id> --output`. That flag had
been renamed to `--logs`, and `--output` is now the *global* format switch which
takes a value — so the documented command doesn't just do the wrong thing, it
errors. Nothing caught it: `test_skill_copies_match.py` only proves the two
copies of the file agree with each other, not that either agrees with the CLI.

This closes that loop. It is the same idea as `api_contract.json` guarding the
CLI against the API: documentation that isn't executed rots, so assert it.
"""
import re
from pathlib import Path

import pytest
import typer.main

from tdt.cli import app

SKILL = Path(__file__).resolve().parents[1] / "tdt" / "skill_asset" / "SKILL.md"

# Flags that belong to the top-level callback and are valid anywhere thanks to
# the argv hoist in tdt/argv.py.
GLOBAL_FLAGS = {"--profile", "-p", "--url", "--bu", "--output", "-o", "--version", "--help"}


def _examples() -> list[str]:
    """Every `tdt …` line inside a fenced code block in the skill."""
    lines = []
    fenced = False
    for raw in SKILL.read_text().splitlines():
        if raw.startswith("```"):
            fenced = not fenced
            continue
        if fenced and raw.strip().startswith("tdt "):
            lines.append(raw.split("#", 1)[0].strip())
    return lines


def _resolve(tokens: list[str]):
    """Walk `tdt a b …` to the deepest real command. Returns (command, path)."""
    cmd = typer.main.get_command(app)
    path = []
    for tok in tokens[1:]:                      # skip the leading "tdt"
        if tok.startswith("-"):
            break
        sub = getattr(cmd, "commands", {}).get(tok)
        if sub is None:
            break
        cmd, _ = sub, path.append(tok)
    return cmd, path


def _declared(cmd) -> set[str]:
    out = set()
    for p in getattr(cmd, "params", []) or []:
        out.update(p.opts or [])
        out.update(p.secondary_opts or [])
    return out


def _value_flags(cmd) -> set[str]:
    """Flags that require a value, i.e. not booleans.

    Needed because a flag can be *spelled* correctly and still be wrong: the
    skill documented `tdt run steps <id> --output`, and `--output` is a real
    global flag — but it takes `table|json`, so with no value the command fails
    to parse. Checking only for the flag's existence let that through.
    """
    out = set()
    root = typer.main.get_command(app)
    for source in (cmd, root):
        for p in getattr(source, "params", []) or []:
            if getattr(p, "is_flag", False) or getattr(p, "count", False):
                continue
            if getattr(p, "param_type_name", None) != "option":
                continue
            out.update(p.opts or [])
    return out


def test_the_skill_actually_contains_examples():
    """Guard the parser: a silent zero here would make every test below vacuous."""
    ex = _examples()
    assert len(ex) >= 10, f"only parsed {len(ex)} examples from {SKILL}"


@pytest.mark.parametrize("line", _examples())
def test_documented_flags_exist_on_the_command(line):
    tokens = line.split()
    cmd, path = _resolve(tokens)
    declared = _declared(cmd) | GLOBAL_FLAGS
    for tok in tokens:
        if not tok.startswith("-") or tok == "--":
            continue
        flag = tok.split("=", 1)[0]
        assert flag in declared, (
            f"SKILL.md documents `{flag}` for `tdt {' '.join(path)}`, "
            f"which does not accept it.\n  line: {line}\n"
            f"  accepts: {sorted(f for f in declared if f.startswith('--'))}"
        )


@pytest.mark.parametrize("line", _examples())
def test_value_taking_flags_are_given_a_value(line):
    """A correctly-spelled flag with a missing value is still a broken example."""
    tokens = line.split()
    cmd, path = _resolve(tokens)
    needs_value = _value_flags(cmd)
    for i, tok in enumerate(tokens):
        if not tok.startswith("-") or tok == "--" or "=" in tok:
            continue
        if tok not in needs_value:
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        assert nxt is not None and not nxt.startswith("-"), (
            f"SKILL.md shows `{tok}` with no value for `tdt {' '.join(path)}`, "
            f"but it requires one.\n  line: {line}"
        )


@pytest.mark.parametrize("line", _examples())
def test_documented_subcommands_exist(line):
    tokens = [t for t in line.split() if not t.startswith("-")]
    cmd = typer.main.get_command(app)
    for tok in tokens[1:]:
        subs = getattr(cmd, "commands", None)
        if not subs:
            break
        if tok in subs:
            cmd = subs[tok]
            continue
        # Not a subcommand — must be a placeholder argument, not a typo.
        assert re.fullmatch(r"<[a-z-]+>|/[\w./-]+|[\w./:-]+", tok), (
            f"SKILL.md references `{tok}` which is neither a subcommand "
            f"nor a placeholder\n  line: {line}"
        )
        break
