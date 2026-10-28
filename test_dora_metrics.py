"""Tests for dora_metrics.py. All data is SYNTHETIC."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import dora_metrics as dm

T0 = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_deploy(day, lead_hours=(10,), failed=False, recovery_minutes=0.0, hotfix=False):
    """Build a SYNTHETIC deployment with exact, known values."""
    deployed_at = T0 + timedelta(days=day)
    return dm.Deployment(
        deploy_id=f"SYN-TEST-{day}",
        deployed_at=deployed_at,
        commit_times=[deployed_at - timedelta(hours=h) for h in lead_hours],
        review_hours=2,
        ci_minutes=12,
        approval_wait_hours=4,
        manual_deploy_minutes=30,
        is_hotfix=hotfix,
        failed=failed,
        recovery_minutes=recovery_minutes,
    )


@pytest.fixture
def known_deployments():
    # 4 deployments over 10 days; 1 failure recovered in 90 min.
    return [
        make_deploy(0, lead_hours=(10, 20)),
        make_deploy(1, lead_hours=(30,)),
        make_deploy(1, lead_hours=(40,), failed=True, recovery_minutes=90),
        make_deploy(5, lead_hours=(50,)),
    ]


def test_deployment_frequency(known_deployments):
    m = dm.calculate_metrics(known_deployments, days=10)
    assert m["total_deployments"] == 4
    assert m["deployments_per_day"] == pytest.approx(0.4)
    assert m["deployments_per_week"] == pytest.approx(2.8)
    assert m["days_with_deployment_pct"] == pytest.approx(30.0)  # 3 distinct days


def test_lead_time_is_median_per_commit(known_deployments):
    m = dm.calculate_metrics(known_deployments, days=10)
    # Commits: 10, 20, 30, 40, 50 h -> median 30 h
    assert m["lead_time_median_hours"] == pytest.approx(30)


def test_change_failure_rate(known_deployments):
    m = dm.calculate_metrics(known_deployments, days=10)
    assert m["change_failure_rate_pct"] == pytest.approx(25.0)


def test_recovery_time(known_deployments):
    m = dm.calculate_metrics(known_deployments, days=10)
    assert m["recovery_median_hours"] == pytest.approx(1.5)


def test_no_failures_gives_none_recovery():
    m = dm.calculate_metrics([make_deploy(0)], days=1)
    assert m["change_failure_rate_pct"] == 0
    assert m["recovery_median_hours"] is None


def test_empty_period():
    m = dm.calculate_metrics([], days=90)
    assert m["deployments_per_day"] == 0
    assert m["change_failure_rate_pct"] is None
    assert m["lead_time_median_hours"] is None


def test_report_handles_period_with_no_deployments():
    report = dm.format_report(dm.calculate_metrics([], days=1))
    assert "n/a (no deployments)" in report


def test_invalid_days_rejected():
    with pytest.raises(ValueError):
        dm.calculate_metrics([], days=0)
    with pytest.raises(ValueError):
        dm.generate_deployments(days=0)


def test_generation_is_reproducible():
    a = dm.calculate_metrics(dm.generate_deployments(90, seed=1), 90)
    b = dm.calculate_metrics(dm.generate_deployments(90, seed=1), 90)
    assert a == b


def test_generated_data_covers_90_days_and_is_synthetic():
    deps = dm.generate_deployments(90)
    assert deps, "expected some deployments"
    span = deps[-1].deployed_at - deps[0].deployed_at
    assert span <= timedelta(days=90)
    assert all(d.deploy_id.startswith("SYN-DEP-") for d in deps)
    assert all(lt > 0 for d in deps for lt in d.lead_times_hours)
    assert all(d.recovery_minutes > 0 for d in deps if d.failed)


@pytest.mark.parametrize("per_day, expected", [
    (2.0, "elite"), (1.0, "high"), (1 / 7, "high"), (0.1, "medium"), (0.01, "low"),
])
def test_classify_deployment_frequency(per_day, expected):
    assert dm.classify_deployment_frequency(per_day).startswith(expected)


@pytest.mark.parametrize("hours, expected", [
    (5, "elite"), (24, "high"), (24 * 7, "medium"), (24 * 60, "low"),
])
def test_classify_lead_time(hours, expected):
    assert dm.classify_lead_time(hours).startswith(expected)


@pytest.mark.parametrize("pct, expected", [(4, "elite"), (9, "medium"), (18, "high"), (35, "low")])
def test_classify_change_failure_rate(pct, expected):
    assert expected in dm.classify_change_failure_rate(pct)


@pytest.mark.parametrize("hours, expected", [
    (0.5, "elite"), (5, "high/medium"), (48, "between"), (24 * 10, "low"),
])
def test_classify_recovery_time(hours, expected):
    assert dm.classify_recovery_time(hours).startswith(expected)


def test_cli_writes_json_and_csv(tmp_path, capsys):
    json_path, csv_path = tmp_path / "m.json", tmp_path / "d.csv"
    dm.main(["--json", str(json_path), "--csv", str(csv_path)])
    out = capsys.readouterr().out
    assert "SYNTHETIC" in out
    data = json.loads(json_path.read_text())
    assert data["synthetic"] is True and data["period_days"] == 90
    lines = csv_path.read_text().splitlines()
    assert lines[0].startswith("deploy_id") and lines[1].endswith("True")
