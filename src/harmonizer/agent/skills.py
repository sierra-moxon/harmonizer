"""Materialize the workflow mapping skills into a job's ``.claude/skills/``.

The mapping *methodology* is delivered to the agent as Claude Code skills:
markdown files with a YAML frontmatter header (``name`` / ``description``)
followed by prose guidance. Per the plan, copy-in applies to markdown skills
**only** — no science-domain skills, and no Python is vendored.

The source skills live in-repo under ``skills/workflow/`` (one ``.md`` per
skill). :func:`write_skills_to_claude_dir` copies the enabled workflow skills
into a job workspace using the standard Claude Code skills layout:

    <job_dir>/.claude/skills/<skill-name>/SKILL.md

Each skill becomes its own subdirectory whose ``SKILL.md`` is the skill body —
the convention the Claude Code CLI discovers skills by. The source filename stem
(e.g. ``nmdc-env-triad``) is used as ``<skill-name>``.

Mirrors OpenScientist's ``agent/skills.py`` (pattern only; authored here).
"""

from __future__ import annotations

from pathlib import Path

#: Directory (relative to the repo root) holding the in-repo workflow skills.
_SKILLS_SUBDIR = ("skills", "workflow")

#: The enabled workflow skills, by source-file stem. Only these are materialized;
#: there are no science-domain skills. Order is the intended reading order.
ENABLED_SKILLS: tuple[str, ...] = (
    "nmdc-curation-rules",
    "nmdc-env-triad",
    "nmdc-taxon-resolution",
    "spreadsheet-to-nmdc",
)

#: Filename each skill is written as inside its skill subdirectory (Claude Code
#: skills convention).
SKILL_FILENAME = "SKILL.md"

#: Subpath, under a job dir, where Claude Code discovers skills.
CLAUDE_SKILLS_SUBPATH = (".claude", "skills")


def _skills_source_dir() -> Path:
    """Locate the in-repo ``skills/workflow/`` directory.

    The skills are stored at the repo root (outside the installed package), so we
    walk up from this module until we find a ``skills/workflow`` directory. This
    is robust to being run from any working directory. Raises
    :class:`FileNotFoundError` if the directory cannot be found.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent.joinpath(*_SKILLS_SUBDIR)
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        f"could not locate in-repo skills directory {'/'.join(_SKILLS_SUBDIR)!r} "
        f"above {__file__!r}"
    )


def write_skills_to_claude_dir(job_dir: Path | str) -> list[Path]:
    """Copy the enabled workflow skills into ``<job_dir>/.claude/skills/``.

    Each enabled skill is written as ``<skill-name>/SKILL.md`` (the standard
    Claude Code skills layout). Directories are created as needed. Only the
    workflow skills in :data:`ENABLED_SKILLS` are materialized — no
    science-domain skills.

    Returns the list of written ``SKILL.md`` paths, in reading order.
    """
    job_dir = Path(job_dir)
    source_dir = _skills_source_dir()
    skills_root = job_dir.joinpath(*CLAUDE_SKILLS_SUBPATH)

    written: list[Path] = []
    for skill_name in ENABLED_SKILLS:
        source = source_dir / f"{skill_name}.md"
        if not source.is_file():
            raise FileNotFoundError(f"missing source skill: {source}")
        skill_dir = skills_root / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        target = skill_dir / SKILL_FILENAME
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(target)

    return written
