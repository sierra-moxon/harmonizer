"""Tests for workflow-skill materialization (Phase 5).

Verify that :func:`write_skills_to_claude_dir` lands the enabled workflow skills
in ``<job_dir>/.claude/skills/<name>/SKILL.md`` with parseable frontmatter, and
that no science-domain skills are materialized.
"""

from __future__ import annotations

from harmonizer.agent import ClaudeCodeAgent, get_agent, write_skills_to_claude_dir
from harmonizer.agent.base import AgentConfig
from harmonizer.agent.skills import (
    CLAUDE_SKILLS_SUBPATH,
    ENABLED_SKILLS,
    SKILL_FILENAME,
)
from harmonizer.providers import AnthropicProvider

#: Skills that must be present; also the full expected set (no others allowed).
_EXPECTED = {
    "nmdc-curation-rules",
    "nmdc-env-triad",
    "nmdc-taxon-resolution",
    "spreadsheet-to-nmdc",
}


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse a minimal ``key: value`` YAML frontmatter block from ``text``.

    Avoids a hard dependency on a YAML parser; asserts the block delimiters and
    returns the top-level scalar keys we care about (``name``, ``description``).
    """
    assert text.startswith("---\n"), "frontmatter must open with '---'"
    _, block, _body = text.split("---\n", 2)
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if line and not line.startswith((" ", "\t")) and ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def _skills_root(job_dir):
    return job_dir.joinpath(*CLAUDE_SKILLS_SUBPATH)


def test_writes_expected_skills(tmp_path):
    written = write_skills_to_claude_dir(tmp_path)

    assert len(written) == len(_EXPECTED)
    names = {p.parent.name for p in written}
    assert names == _EXPECTED


def test_layout_is_skill_subdir_with_skill_md(tmp_path):
    write_skills_to_claude_dir(tmp_path)
    skills_root = _skills_root(tmp_path)

    assert skills_root.is_dir()
    for name in _EXPECTED:
        skill_md = skills_root / name / SKILL_FILENAME
        assert skill_md.is_file(), f"{name} missing SKILL.md"


def test_only_workflow_skills_no_science_domain(tmp_path):
    write_skills_to_claude_dir(tmp_path)
    skills_root = _skills_root(tmp_path)

    materialized = {p.name for p in skills_root.iterdir() if p.is_dir()}
    assert materialized == _EXPECTED
    # No leaked science-domain skills from other agents.
    for banned in ("phenix", "single-cell", "search-pubmed", "hypotheses"):
        assert banned not in materialized


def test_frontmatter_parseable(tmp_path):
    written = write_skills_to_claude_dir(tmp_path)

    for path in written:
        fields = _parse_frontmatter(path.read_text(encoding="utf-8"))
        assert "name" in fields and fields["name"]
        assert "description" in fields
        # The frontmatter name matches the skill directory name.
        assert fields["name"] == path.parent.name


def test_enabled_skills_constant_matches_expected():
    assert set(ENABLED_SKILLS) == _EXPECTED


def test_idempotent_rewrite(tmp_path):
    first = write_skills_to_claude_dir(tmp_path)
    second = write_skills_to_claude_dir(tmp_path)
    assert first == second
    for path in second:
        assert path.is_file()


def test_prepare_job_workspace_materializes_skills(tmp_path):
    config = AgentConfig(
        job_id="job-skills",
        job_dir=tmp_path / "jobs" / "job-skills",
        provider=AnthropicProvider(api_key="sk-test", model="m"),
        database_url="sqlite:///test.db",
    )
    agent = get_agent(config)
    assert isinstance(agent, ClaudeCodeAgent)

    agent.prepare_job_workspace()

    skills_root = _skills_root(config.job_dir)
    materialized = {p.name for p in skills_root.iterdir() if p.is_dir()}
    assert materialized == _EXPECTED
