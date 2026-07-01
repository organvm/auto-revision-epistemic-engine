"""Tests for the package command-line entry point."""

import argparse
import json
import sys

import auto_revision_epistemic_engine
from auto_revision_epistemic_engine import __main__ as cli


def _last_json_object(output):
    decoder = json.JSONDecoder()
    position = 0
    last = None
    while position < len(output):
        while position < len(output) and output[position].isspace():
            position += 1
        if position >= len(output):
            break
        last, position = decoder.raw_decode(output, position)
    return last


def test_main_without_command_prints_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["auto-revision-epistemic-engine"])

    assert cli.main() == 0

    captured = capsys.readouterr()
    assert "Available commands" in captured.out
    assert "run" in captured.out


def test_run_command_parses_inline_inputs(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "auto-revision-epistemic-engine",
            "run",
            "--pipeline-id",
            "cli-inline",
            "--seed",
            "321",
            "--inputs",
            '{"records": 5, "data": {"kind": "inline"}}',
            "--no-hrg",
            "--no-ethics",
            "--audit-dir",
            str(tmp_path / "audit"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert cli.main() == 0

    result = json.loads(capsys.readouterr().out)
    assert result["success"] is True
    assert result["pipeline_status"]["pipeline_id"] == "cli-inline"
    assert "hrg_stats" not in result["pipeline_status"]
    assert "ethics_compliance" not in result["pipeline_status"]


def test_run_command_uses_inputs_file_config(tmp_path, capsys):
    inputs_file = tmp_path / "pipeline.json"
    inputs_file.write_text(
        json.dumps(
            {
                "pipeline_id": "file-pipeline",
                "random_seed": 987,
                "inputs": {"records": 4},
                "hrg_config": {"auto_approve": True},
                "ethics_config": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        inputs=None,
        inputs_file=str(inputs_file),
        pipeline_id="ignored",
        seed=None,
        no_hrg=True,
        no_ethics=False,
        audit_dir=str(tmp_path / "audit"),
        state_dir=str(tmp_path / "state"),
    )

    assert cli.cmd_run(args) == 0

    result = json.loads(capsys.readouterr().out)
    status = result["pipeline_status"]
    assert status["pipeline_id"] == "file-pipeline"
    assert status["reproducibility"]["random_seed"] == 987
    assert status["hrg_stats"]["total_reviews"] == 4
    assert "ethics_compliance" not in status


def test_status_command_reports_initialized_pipeline(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "auto-revision-epistemic-engine",
            "status",
            "--pipeline-id",
            "status-cli",
            "--seed",
            "42",
            "--audit-dir",
            str(tmp_path / "audit"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert cli.main() == 0

    status = json.loads(capsys.readouterr().out)
    assert status["pipeline_id"] == "status-cli"
    assert status["started"] is False
    assert status["completed"] is False
    assert status["phase_status"]["status"] == "NOT_STARTED"


def test_audit_command_after_run_verifies_chain(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "auto-revision-epistemic-engine",
            "audit",
            "--after-run",
            "--audit-dir",
            str(tmp_path / "audit"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
    )

    assert cli.main() == 0

    captured = capsys.readouterr()
    audit = json.loads(captured.out)
    assert audit["chain_valid"] is True
    assert "[OK] Audit chain integrity verified." in captured.err


def test_audit_command_returns_failure_for_invalid_chain(monkeypatch, tmp_path, capsys):
    class FakeEngine:
        def __init__(self, **_kwargs):
            pass

        def execute(self, inputs=None):
            return {"success": True, "inputs": inputs}

        def get_audit_trail(self):
            return {"chain_valid": False, "total_entries": 0}

    monkeypatch.setattr(auto_revision_epistemic_engine, "AutoRevisionEngine", FakeEngine)
    args = argparse.Namespace(
        pipeline_id=None,
        after_run=False,
        audit_dir=str(tmp_path / "audit"),
        state_dir=str(tmp_path / "state"),
    )

    assert cli.cmd_audit(args) == 1

    captured = capsys.readouterr()
    assert json.loads(captured.out)["chain_valid"] is False
    assert "[FAIL] Audit chain integrity BROKEN." in captured.err


def test_demo_command_prints_reports_and_completion(monkeypatch, tmp_path, capsys):
    demo_dirs = iter([tmp_path / "demo-audit", tmp_path / "demo-state"])
    monkeypatch.setattr(cli.tempfile, "mkdtemp", lambda prefix: str(next(demo_dirs)))

    assert cli.cmd_demo(argparse.Namespace()) == 0

    output = capsys.readouterr().out
    assert '"stage": "init"' in output
    assert '"stage": "reports"' in output
    final_message = _last_json_object(output)
    assert final_message["stage"] == "complete"
    assert final_message["audit_dir"] == str(tmp_path / "demo-audit")
    assert final_message["state_dir"] == str(tmp_path / "demo-state")
