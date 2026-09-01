# `tdt` — Terraducktel CLI

The intended way to drive TDT from a terminal, from CI, or from an agent.
Replaces hand-rolled `curl` loops and the older `tdt-flow.sh`.

```bash
uv tool install ./services/cli
tdt profile add prod --url https://tdt.example.com --bu <slug> --default
tdt login
tdt run apply my-workspace --auto-approve --max-destroy 0
```

Full command reference: `tdt --help`, and `tdt <group> --help`.

**Skill for agents.** Inside this repo it is already active at
[`.claude/skills/tdt/`](../../.claude/skills/tdt/SKILL.md) — a clone needs no
install step. Elsewhere, `tdt skill install` drops the same file into
`~/.claude/skills/tdt/`. The two copies are kept byte-identical by
`services/api/tests/test_skill_copies_match.py`; edit either and copy it over.

**Adding an API call?** Declare it in [`api_contract.json`](./api_contract.json)
— two tests use that file to keep the CLI and the API from drifting apart.

```bash
cd services/cli && python -m pytest tests/ -q
```
