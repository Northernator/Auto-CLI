"""Validation must include workflow tests added after the initial scaffold."""

from auto_cli import evidence, scaffold, surface, validate


def test_validation_runs_added_workflow_test_files(tmp_path):
    bundle = evidence.EvidenceBundle(target="workflow-test-target")
    root = scaffold.generate(surface.classify(bundle), bundle, tmp_path / "out", name="workflow-app")
    added = root / "cli_anything" / "workflow_app" / "tests" / "test_workflows.py"
    added.write_text(
        'def test_added_workflow():\n    assert False, "added workflow regression was collected"\n',
        encoding="utf-8",
    )
    report = validate.validate(root)
    tests = next(check for check in report.checks if check.name == "unit tests pass")
    assert not tests.ok
    assert "1 failed" in tests.detail
