"""The two SKILL.md copies must stay byte-identical.

There are deliberately two:

* `services/cli/tdt/skill_asset/SKILL.md` — ships inside the wheel, so
  `tdt skill install` works for anyone who installed the CLI from PyPI or a
  path, with no repo checkout.
* `.claude/skills/tdt/SKILL.md` — project-scoped, so a fresh clone of this repo
  picks the skill up with no install step at all.

A duplicated file is normally the wrong answer, and the whole point of this CLI
was that hand-maintained copies of the API drift. This test is what makes the
duplication safe: the moment the two diverge, CI says so and names both paths.

(A symlink would avoid the copy, but skill discovery following symlinks is not
something this repo should depend on, and symlinks do not survive every
checkout. A test is boring and always works.)
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PACKAGED = REPO / "services" / "cli" / "tdt" / "skill_asset" / "SKILL.md"
PROJECT = REPO / ".claude" / "skills" / "tdt" / "SKILL.md"


def test_both_copies_exist():
    assert PACKAGED.exists(), f"packaged skill missing at {PACKAGED}"
    assert PROJECT.exists(), f"project-scoped skill missing at {PROJECT}"


def test_copies_are_identical():
    packaged = PACKAGED.read_text()
    project = PROJECT.read_text()
    assert packaged == project, (
        "SKILL.md copies have drifted.\n"
        f"  packaged: {PACKAGED}\n"
        f"  project : {PROJECT}\n"
        "Copy whichever you edited over the other:\n"
        "  cp services/cli/tdt/skill_asset/SKILL.md .claude/skills/tdt/SKILL.md"
    )


def test_skill_has_frontmatter_with_a_name_and_description():
    """A skill without frontmatter is silently not discovered."""
    text = PROJECT.read_text()
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    end = text.index("\n---\n", 3)
    front = text[4:end]
    assert "name:" in front, "frontmatter needs a name:"
    assert "description:" in front, "frontmatter needs a description:"


def test_skill_does_not_tell_readers_to_hand_roll_curl():
    """The skill exists to route people to the CLI; a curl recipe creeping back
    in would recreate the drift problem it was written to fix."""
    text = PROJECT.read_text()
    assert "curl -s" not in text and "curl -X" not in text, (
        "SKILL.md contains a curl recipe — point at a `tdt` command instead"
    )
