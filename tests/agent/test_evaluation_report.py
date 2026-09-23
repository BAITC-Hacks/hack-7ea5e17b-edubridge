from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from wind_agent.agent.evaluation_report import evaluation_report
from wind_agent.config import Settings, load_settings


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "models" / "wind-power-v1"


@pytest.fixture
def package(tmp_path):
    destination = tmp_path / "model"
    destination.mkdir()
    for name in ("metadata.json", "validation.json", "model.joblib"):
        shutil.copyfile(PACKAGE / name, destination / name)
    return destination


def settings(package):
    return Settings(mode="archive", model_artifact_path=package)


def rewrite_report(package, mutation, *, update_embedded=False):
    report = json.loads((package / "validation.json").read_text(encoding="utf-8"))
    mutation(report)
    (package / "validation.json").write_text(json.dumps(report), encoding="utf-8")
    if update_embedded:
        metadata = json.loads((package / "metadata.json").read_text(encoding="utf-8"))
        metadata["metadata"]["validation"] = deepcopy(report)
        (package / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


def test_real_package_preserves_holdout_baseline_and_refit_distinction():
    result = evaluation_report(load_settings(ROOT / "config" / "archive-model.json"))
    assert result["status"] == "ready", result["reason"]
    assert result["model_version"] == "wind-power-v1"
    assert result["selected_candidate"]["name"] == "hgb_medium"
    assert result["test_truth_available"] is False
    assert result["is_demo"] is False
    metrics = result["metrics"]
    assert metrics["tuning"]["scores"]["overall"]["mae"] == pytest.approx(0.2324636288)
    assert metrics["independent_holdout"]["scores"]["overall"]["mae"] == pytest.approx(0.2269187055)
    assert metrics["independent_holdout"]["used_for_selection"] is False
    assert metrics["independent_holdout"]["training_cutoff"] == "2026-01-14T19:00:00+00:00"
    assert metrics["production_refit"]["training_cutoff"] == "2026-01-31T18:00:00+00:00"
    assert metrics["production_refit"]["includes_holdout"] is True
    assert metrics["production_refit"]["independent_metrics"] is None
    comparison = result["baseline"]["comparison"]
    assert comparison["wind_curve_mae"] == pytest.approx(0.2094604765)
    assert comparison["wind_curve_better"] is True
    assert any("lower independent-holdout MAE" in warning for warning in result["warnings"])
    assert result["periods"]["target_start_exclusive"] is True
    assert result["periods"]["target_end_inclusive"] is True
    assert len(result["provenance"]["model_sha256"]) == 64


def test_demo_never_reads_or_exposes_configured_real_metrics(monkeypatch):
    def forbidden_read(*args, **kwargs):
        pytest.fail("Demo evaluation must not read the configured real model package")
    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    result = evaluation_report(Settings(mode="demo", model_artifact_path=PACKAGE))
    assert result["status"] == "unavailable"
    assert result["is_demo"] is True
    assert result["metrics"] is result["baseline"] is None


@pytest.mark.parametrize("missing", ["metadata.json", "validation.json", "model.joblib"])
def test_missing_package_file_is_unavailable_not_zero_metrics(package, missing):
    (package / missing).unlink()
    result = evaluation_report(settings(package))
    assert result["status"] == "unavailable"
    assert missing in result["reason"]
    assert result["metrics"] is result["baseline"] is None


def test_unconfigured_package_is_unavailable():
    assert evaluation_report(Settings())["status"] == "unavailable"


def test_metadata_file_configuration_finds_sibling_report(package):
    result = evaluation_report(settings(package / "metadata.json"))
    assert result["status"] == "ready", result["reason"]


def test_modified_validation_cannot_be_attached_to_unchanged_package(package):
    rewrite_report(package, lambda report: report["holdout"]["hgb_medium"]["overall"].update(mae=0.0))
    result = evaluation_report(settings(package))
    assert result["status"] == "invalid"
    assert "embedded" in result["reason"]
    assert result["metrics"] is None


def test_modified_model_bytes_invalidate_report_association(package):
    with (package / "model.joblib").open("ab") as stream:
        stream.write(b"tampered weights")
    result = evaluation_report(settings(package))
    assert result["status"] == "invalid"
    assert "SHA256" in result["reason"]
    assert result["metrics"] is None


@pytest.mark.parametrize("field,value,reason", [
    ("holdout_used_for_selection", True, "independent holdout"),
    ("production_training_cutoff", "2026-01-30T18:00:00Z", "cutoff mismatch"),
    ("february_ground_truth_available", True, "February-truth"),
])
def test_incompatible_scope_is_not_reported_as_ready(package, field, value, reason):
    rewrite_report(package, lambda report: report.update({field: value}), update_embedded=True)
    result = evaluation_report(settings(package))
    assert result["status"] == "invalid"
    assert reason in result["reason"]
    assert result["metrics"] is result["baseline"] is None


def test_foreign_model_version_is_rejected_even_with_matching_embedded_copy(package):
    rewrite_report(package, lambda report: report["design"].update(model_version="different-model"),
                   update_embedded=True)
    result = evaluation_report(settings(package))
    assert result["status"] == "invalid"
    assert "model_version" in result["reason"]


def test_source_hash_mismatch_is_rejected(package):
    rewrite_report(package, lambda report: report["source_sha256"].update(turbine_1="f" * 64),
                   update_embedded=True)
    result = evaluation_report(settings(package))
    assert result["status"] == "invalid"
    assert "Source hash" in result["reason"]


@pytest.mark.parametrize("content", ["{bad JSON", "[]", '{"mae": NaN}'])
def test_malformed_report_returns_explicit_error(package, content):
    (package / "validation.json").write_text(content, encoding="utf-8")
    result = evaluation_report(settings(package))
    assert result["status"] == "invalid"
    assert result["reason"].startswith("Invalid model evaluation report:")
    assert result["metrics"] is result["baseline"] is None


def test_invalid_metric_value_is_not_coerced_to_numeric(package):
    rewrite_report(package, lambda report: report["holdout"]["hgb_medium"]["overall"].update(mae="0.0"),
                   update_embedded=True)
    result = evaluation_report(settings(package))
    assert result["status"] == "invalid"
    assert "finite nonnegative number" in result["reason"]


def test_overlapping_windows_cannot_be_labelled_independent(package):
    rewrite_report(package, lambda report: report["splits"]["split_config"]["windows"]["holdout"].update(
        target_start="2026-01-10T19:00:00Z"), update_embedded=True)
    result = evaluation_report(settings(package))
    assert result["status"] == "invalid"
    assert "overlap" in result["reason"]
