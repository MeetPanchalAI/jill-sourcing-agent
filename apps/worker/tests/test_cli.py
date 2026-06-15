"""P8: CLI smoke. ``jill demo`` runs the whole pipeline in-memory — exercise it
through Typer's runner (no servers) and check it surfaces ranked fit leads."""

from __future__ import annotations

from jill.cli import _load_icp, _seed_companies, app
from typer.testing import CliRunner

runner = CliRunner()


def test_demo_command_runs_end_to_end():
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0, result.output
    assert "fit" in result.output
    assert "Ranked leads" in result.output
    assert "Alice Nguyen" in result.output  # a fit lead from the Vapi fixtures
    assert "drafts staged for approval" in result.output


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("demo", "health", "source", "leads", "role", "outreach"):
        assert cmd in result.output


def test_load_icp_inline_and_seed_extraction():
    icp = _load_icp('{"target_companies": [{"name": "Vapi"}, "Retell AI"]}')
    assert _seed_companies(icp) == ["Vapi", "Retell AI"]
