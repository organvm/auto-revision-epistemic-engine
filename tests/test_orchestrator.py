"""Focused tests for the core orchestrator governance flow."""

from types import SimpleNamespace

import pytest

from auto_revision_epistemic_engine.core.orchestrator import (
    Orchestrator,
    PipelineConfig,
)
from auto_revision_epistemic_engine.hrg.human_review_gate import ReviewStatus
from auto_revision_epistemic_engine.phases.phase_manager import (
    PhaseName,
    PhaseStatus,
)


def _config(tmp_path, **overrides):
    defaults = {
        "pipeline_id": "orchestrator-test",
        "random_seed": 123,
        "audit_log_dir": str(tmp_path / "audit"),
        "state_dir": str(tmp_path / "state"),
    }
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def _event_types(orchestrator):
    return [entry.event_type for entry in orchestrator.audit_logger.get_entries()]


def test_pipeline_runs_all_phases_with_governance_artifacts(tmp_path):
    orchestrator = Orchestrator(_config(tmp_path))

    result = orchestrator.execute_pipeline(
        {"records": 7, "data": {"source": "unit-test"}}
    )

    assert result["success"] is True
    assert result["outputs"]["final_output"] == {"status": "finalized"}
    assert orchestrator.pipeline_started is True
    assert orchestrator.pipeline_completed is True

    status = result["pipeline_status"]
    assert status["phase_status"]["status"] == "COMPLETED"
    assert status["phase_status"]["phases_completed"] == len(PhaseName)
    assert status["hrg_stats"]["total_reviews"] == 4
    assert status["hrg_stats"]["by_status"][ReviewStatus.APPROVED.value] == 4
    assert status["resource_stats"]["count"] == len(PhaseName) * 2
    assert status["ethics_compliance"]["total_audits"] == len(PhaseName) * 2
    assert status["reproducibility"]["snapshots_count"] == len(PhaseName)
    assert status["audit_chain_valid"] is True

    gate_phases = {
        PhaseName.INGESTION,
        PhaseName.PROCESSING,
        PhaseName.VALIDATION,
        PhaseName.FINALIZATION,
    }
    executions = orchestrator.phase_manager.get_phase_executions()
    assert all(ex.status == PhaseStatus.COMPLETED for ex in executions)
    assert {
        ex.phase for ex in executions if ex.hrg_review_id is not None
    } == gate_phases

    attestation_types = {
        att.attestation_type for att in orchestrator.audit_logger.get_attestations()
    }
    assert attestation_types == {
        "RESOURCE_COMPLIANCE",
        "ETHICS_COMPLIANCE",
        "REPRODUCIBILITY",
    }
    assert "HRG_REVIEW_REQUESTED" in _event_types(orchestrator)


def test_disabled_governance_components_leave_only_reproducibility_status(tmp_path):
    orchestrator = Orchestrator(
        _config(
            tmp_path,
            enable_hrg=False,
            enable_ethics_audit=False,
            enable_resource_tracking=False,
        )
    )

    result = orchestrator.execute_pipeline({"records": 3})
    status = result["pipeline_status"]

    assert result["success"] is True
    assert "hrg_stats" not in status
    assert "resource_stats" not in status
    assert "ethics_compliance" not in status
    assert status["reproducibility"]["snapshots_count"] == len(PhaseName)
    assert orchestrator.hrg is None
    assert orchestrator.rol_t is None
    assert orchestrator.ethics is None

    orchestrator._allocate_phase_resources(PhaseName.INGESTION, "no-op")
    orchestrator._record_phase_resource_usage(PhaseName.INGESTION, "no-op")
    orchestrator._simulate_hrg_approval("no-op")

    attestations = orchestrator.audit_logger.get_attestations()
    assert [att.attestation_type for att in attestations] == ["REPRODUCIBILITY"]


def test_execute_pipeline_stops_and_logs_failed_phase(tmp_path, monkeypatch):
    orchestrator = Orchestrator(
        _config(
            tmp_path,
            enable_hrg=False,
            enable_ethics_audit=False,
            enable_resource_tracking=False,
        )
    )
    original_logic = orchestrator._execute_phase_logic

    def fail_processing(phase, inputs):
        if phase == PhaseName.PROCESSING:
            raise RuntimeError("processing blew up")
        return original_logic(phase, inputs)

    monkeypatch.setattr(orchestrator, "_execute_phase_logic", fail_processing)

    result = orchestrator.execute_pipeline({"records": 2})

    assert result == {
        "success": False,
        "failed_at_phase": PhaseName.PROCESSING.value,
        "error": "processing blew up",
    }
    assert orchestrator.pipeline_completed is False

    status = orchestrator.get_pipeline_status()["phase_status"]
    assert status["status"] == "FAILED"
    assert status["phases_completed"] == 2
    assert status["status_breakdown"][PhaseStatus.FAILED.value] == 1

    failed = orchestrator.phase_manager.get_phase_executions(
        phase=PhaseName.PROCESSING,
        status=PhaseStatus.FAILED,
    )
    assert len(failed) == 1
    assert failed[0].error == "processing blew up"
    assert "PHASE_FAILED" in _event_types(orchestrator)
    assert "PIPELINE_FAILED" in _event_types(orchestrator)


def test_pre_phase_ethics_violation_blocks_before_snapshot(tmp_path, monkeypatch):
    orchestrator = Orchestrator(
        _config(tmp_path, enable_hrg=False, enable_resource_tracking=False)
    )

    def violating_audit(_phase, _context, _stage):
        return SimpleNamespace(violations=[{"axiom_id": "ACCT_001"}])

    monkeypatch.setattr(orchestrator, "_conduct_ethics_audit", violating_audit)

    result = orchestrator._execute_phase(PhaseName.INGESTION, {})

    assert result == {
        "success": False,
        "error": "Ethics violation (PRE): ACCT_001",
    }
    executions = orchestrator.phase_manager.get_phase_executions()
    assert len(executions) == 1
    assert executions[0].status == PhaseStatus.FAILED
    assert executions[0].outputs == {}
    assert orchestrator.state_manager.get_reproducibility_info()["snapshots_count"] == 0
    assert "ETHICS_VIOLATION" in _event_types(orchestrator)


def test_post_phase_ethics_violation_fails_after_snapshot(tmp_path, monkeypatch):
    orchestrator = Orchestrator(
        _config(tmp_path, enable_hrg=False, enable_resource_tracking=False)
    )

    def staged_audit(_phase, _context, stage):
        violations = [{"axiom_id": "SAFE_001"}] if stage == "POST_PHASE" else []
        return SimpleNamespace(violations=violations)

    monkeypatch.setattr(orchestrator, "_conduct_ethics_audit", staged_audit)

    result = orchestrator._execute_phase(PhaseName.ANALYSIS, {"data": {"x": 1}})

    assert result == {
        "success": False,
        "error": "Ethics violation (POST): SAFE_001",
    }
    executions = orchestrator.phase_manager.get_phase_executions()
    assert len(executions) == 1
    assert executions[0].status == PhaseStatus.FAILED
    assert executions[0].outputs["analysis_results"] == {"status": "analyzed"}
    assert orchestrator.state_manager.get_reproducibility_info()["snapshots_count"] == 1
    assert "ETHICS_VIOLATION" in _event_types(orchestrator)


@pytest.mark.parametrize(
    ("phase", "inputs", "expected"),
    [
        (PhaseName.INGESTION, {"records": 11}, {"ingested_records": 11}),
        (
            PhaseName.PREPROCESSING,
            {"ingested_records": 11},
            {"preprocessed_records": 11},
        ),
        (
            PhaseName.PROCESSING,
            {"preprocessed_records": 11},
            {"processed_records": 11},
        ),
        (
            PhaseName.ANALYSIS,
            {},
            {"analysis_results": {"status": "analyzed"}},
        ),
        (PhaseName.VALIDATION, {}, {"validation_passed": True}),
        (
            PhaseName.SYNTHESIS,
            {},
            {"synthesized_output": {"status": "synthesized"}},
        ),
        (PhaseName.REVIEW, {}, {"review_status": "reviewed"}),
        (
            PhaseName.FINALIZATION,
            {},
            {"final_output": {"status": "finalized"}},
        ),
    ],
)
def test_phase_logic_emits_phase_specific_outputs(tmp_path, phase, inputs, expected):
    orchestrator = Orchestrator(
        _config(
            tmp_path,
            enable_hrg=False,
            enable_ethics_audit=False,
            enable_resource_tracking=False,
        )
    )

    outputs = orchestrator._execute_phase_logic(phase, inputs)

    assert outputs["phase"] == phase.value
    assert outputs["processed"] is True
    assert outputs["data"] == inputs.get("data", {})
    for key, value in expected.items():
        assert outputs[key] == value
