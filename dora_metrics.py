"""Generate 90 days of SYNTHETIC HealthQueue deployment data and calculate
the four DORA software delivery metrics.

All data produced by this script is synthetic (randomly generated with a
fixed seed). It does not describe real deployments, people or patients.

Usage:
    python dora_metrics.py                     # print report
    python dora_metrics.py --json report.json  # also save metrics as JSON
    python dora_metrics.py --csv deploys.csv   # also save the raw synthetic data
    python dora_metrics.py --days 90 --seed 365

Uses only the Python standard library.
"""

import argparse
import csv
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median

DEFAULT_DAYS = 90
DEFAULT_SEED = 365
DEFAULT_END = datetime(2026, 10, 27, tzinfo=timezone.utc)


@dataclass
class Deployment:
    """One SYNTHETIC production deployment."""

    deploy_id: str
    deployed_at: datetime
    commit_times: list            # when each included commit was made
    review_hours: float           # waiting for / doing code review
    ci_minutes: float             # automated test + build + scan
    approval_wait_hours: float    # waiting for a MANUAL release approval
    manual_deploy_minutes: float  # hands-on deploy steps
    is_hotfix: bool = False
    failed: bool = False          # needed immediate intervention (rollback/hotfix)
    recovery_minutes: float = 0.0 # time from failure to service restored
    stage_notes: dict = field(default_factory=dict)

    @property
    def lead_times_hours(self):
        """Lead time for each commit: commit -> running in production."""
        return [(self.deployed_at - c).total_seconds() / 3600 for c in self.commit_times]


# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------

def _business_hour(rng, day):
    """A random time between 09:00 and 17:00 UTC on the given day."""
    return day.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(
        minutes=rng.randint(0, 8 * 60)
    )


def generate_deployments(days=DEFAULT_DAYS, seed=DEFAULT_SEED, end=DEFAULT_END):
    """Create a reproducible list of SYNTHETIC deployments over `days` days.

    The model reflects a small team with a partly manual release process:
    automated CI, but manual approval and manual deploy steps.
    """
    if days < 1:
        raise ValueError("days must be at least 1")

    rng = random.Random(seed)
    start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    deployments = []
    counter = 0

    for offset in range(days):
        day = start + timedelta(days=offset)
        weekday = day.weekday() < 5
        # Releases mostly happen on weekdays; manual process limits volume.
        planned = rng.choices([0, 1, 2], weights=[45, 40, 15])[0] if weekday else 0

        for _ in range(planned):
            counter += 1
            deployed_at = _business_hour(rng, day)

            review_hours = rng.lognormvariate(1.8, 0.7)        # median ~6 h
            ci_minutes = rng.uniform(8, 15)
            approval_wait_hours = rng.expovariate(1 / 18)      # mean 18 h (manual)
            manual_deploy_minutes = rng.uniform(20, 45)
            pipeline_hours = (
                review_hours + ci_minutes / 60 + approval_wait_hours + manual_deploy_minutes / 60
            )

            # Each deployment batches 1-6 commits written over the preceding hours.
            commits = [
                deployed_at - timedelta(hours=pipeline_hours + rng.uniform(0, 30))
                for _ in range(rng.randint(1, 6))
            ]

            dep = Deployment(
                deploy_id=f"SYN-DEP-{counter:04d}",
                deployed_at=deployed_at,
                commit_times=sorted(commits),
                review_hours=review_hours,
                ci_minutes=ci_minutes,
                approval_wait_hours=approval_wait_hours,
                manual_deploy_minutes=manual_deploy_minutes,
            )

            # Bigger batches fail more often (a well-known DORA finding).
            fail_chance = 0.08 + 0.025 * len(commits)
            if rng.random() < fail_chance:
                dep.failed = True
                detect = rng.uniform(5, 60)                    # alert -> human notices
                restore = rng.lognormvariate(4.0, 0.9)         # median ~55 min (manual)
                dep.recovery_minutes = detect + restore
            deployments.append(dep)

            # About 40% of failures are fixed with a hotfix deployment
            # (the rest by rollback). Hotfixes are deployments too.
            if dep.failed and rng.random() < 0.4:
                counter += 1
                fix_time = deployed_at + timedelta(minutes=dep.recovery_minutes)
                deployments.append(
                    Deployment(
                        deploy_id=f"SYN-DEP-{counter:04d}",
                        deployed_at=fix_time,
                        commit_times=[fix_time - timedelta(minutes=dep.recovery_minutes * 0.8)],
                        review_hours=0.3,
                        ci_minutes=ci_minutes,
                        approval_wait_hours=0.0,   # emergency path skips approval
                        manual_deploy_minutes=manual_deploy_minutes,
                        is_hotfix=True,
                    )
                )

    deployments.sort(key=lambda d: d.deployed_at)
    return deployments


# ---------------------------------------------------------------------------
# DORA metrics
# ---------------------------------------------------------------------------

def calculate_metrics(deployments, days):
    """Calculate the four DORA metrics from a list of deployments."""
    if days < 1:
        raise ValueError("days must be at least 1")

    total = len(deployments)
    failed = [d for d in deployments if d.failed]
    lead_times = [h for d in deployments for h in d.lead_times_hours]
    recovery_hours = [d.recovery_minutes / 60 for d in failed]
    days_with_deploy = len({d.deployed_at.date() for d in deployments})

    return {
        "period_days": days,
        "total_deployments": total,
        "hotfix_deployments": sum(d.is_hotfix for d in deployments),
        "failed_deployments": len(failed),
        # 1. Deployment frequency
        "deployments_per_day": total / days,
        "deployments_per_week": total / days * 7,
        "days_with_deployment_pct": days_with_deploy / days * 100,
        # 2. Lead time for changes (commit -> production), per commit
        "lead_time_median_hours": median(lead_times) if lead_times else None,
        "lead_time_p90_hours": _percentile(lead_times, 90),
        # 3. Change failure rate
        "change_failure_rate_pct": (len(failed) / total * 100) if total else None,
        # 4. Failed deployment recovery time
        "recovery_median_hours": median(recovery_hours) if recovery_hours else None,
        "recovery_p90_hours": _percentile(recovery_hours, 90),
        # Where lead time goes (helps find manual work)
        "stage_median_hours": _stage_medians(deployments),
    }


def _percentile(values, pct):
    """Nearest-rank percentile (simple and dependency-free)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, round(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _stage_medians(deployments):
    planned = [d for d in deployments if not d.is_hotfix]
    if not planned:
        return {}
    return {
        "code_review": median(d.review_hours for d in planned),
        "automated_ci": median(d.ci_minutes / 60 for d in planned),
        "manual_approval_wait": median(d.approval_wait_hours for d in planned),
        "manual_deploy_steps": median(d.manual_deploy_minutes / 60 for d in planned),
    }


# ---------------------------------------------------------------------------
# Benchmarks: DORA 2024 Accelerate State of DevOps report performance clusters
# ---------------------------------------------------------------------------

# Change failure rate per cluster in the 2024 report. Note it is NOT monotonic:
# the 2024 "high" cluster (20%) had a higher rate than "medium" (10%).
CFR_BENCHMARKS = {"elite": 5.0, "medium": 10.0, "high": 20.0, "low": 40.0}


def classify_deployment_frequency(per_day):
    if per_day > 1:
        return "elite (on demand, multiple per day)"
    if per_day >= 1 / 7:
        return "high (between once per day and once per week)"
    if per_day >= 1 / 30:
        return "medium (between once per week and once per month)"
    return "low (less than once per month)"


def classify_lead_time(hours):
    if hours is None:
        return "n/a"
    if hours < 24:
        return "elite (less than one day)"
    if hours < 24 * 7:
        return "high (between one day and one week)"
    if hours < 24 * 30:
        return "medium (between one week and one month)"
    return "low (between one month and six months)"


def classify_change_failure_rate(pct):
    """Closest 2024 cluster value (the clusters are not ordered thresholds)."""
    if pct is None:
        return "n/a"
    closest = min(CFR_BENCHMARKS, key=lambda k: abs(CFR_BENCHMARKS[k] - pct))
    return f"closest to {closest} cluster ({CFR_BENCHMARKS[closest]:.0f}%)"


def classify_recovery_time(hours):
    if hours is None:
        return "n/a (no failures)"
    if hours < 1:
        return "elite (less than one hour)"
    if hours < 24:
        return "high/medium (less than one day)"
    if hours < 24 * 7:
        return "between medium and low (one day to one week)"
    return "low (one week to one month or more)"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _fmt_hours(hours):
    if hours is None:
        return "n/a"
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"


def format_report(m):
    s = m["stage_median_hours"]
    # Short periods (e.g. --days 1 on a weekend) can have zero deployments.
    cfr = m["change_failure_rate_pct"]
    cfr_text = "n/a (no deployments)" if cfr is None else f"{cfr:.1f}% of deployments needed immediate intervention"
    lines = [
        "HealthQueue DORA metrics  -- SYNTHETIC DATA --",
        f"Period: {m['period_days']} days | deployments: {m['total_deployments']} "
        f"(hotfixes: {m['hotfix_deployments']}, failed: {m['failed_deployments']})",
        "",
        "1. Deployment frequency",
        f"   {m['deployments_per_day']:.2f}/day = {m['deployments_per_week']:.1f}/week; "
        f"deployed on {m['days_with_deployment_pct']:.0f}% of days",
        f"   DORA 2024: {classify_deployment_frequency(m['deployments_per_day'])}",
        "2. Lead time for changes (commit -> production)",
        f"   median {_fmt_hours(m['lead_time_median_hours'])}, p90 {_fmt_hours(m['lead_time_p90_hours'])}",
        f"   DORA 2024: {classify_lead_time(m['lead_time_median_hours'])}",
        "3. Change failure rate",
        f"   {cfr_text}",
        f"   DORA 2024: {classify_change_failure_rate(m['change_failure_rate_pct'])}",
        "4. Failed deployment recovery time",
        f"   median {_fmt_hours(m['recovery_median_hours'])}, p90 {_fmt_hours(m['recovery_p90_hours'])}",
        f"   DORA 2024: {classify_recovery_time(m['recovery_median_hours'])}",
        "",
        "Median time per pipeline stage (planned deployments):",
    ]
    for stage, hours in s.items():
        lines.append(f"   {stage:<22} {_fmt_hours(hours)}")
    return "\n".join(lines)


def write_csv(deployments, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "deploy_id", "deployed_at_utc", "commits", "median_lead_time_hours",
            "is_hotfix", "failed", "recovery_minutes", "synthetic",
        ])
        for d in deployments:
            writer.writerow([
                d.deploy_id, d.deployed_at.isoformat(), len(d.commit_times),
                round(median(d.lead_times_hours), 2), d.is_hotfix, d.failed,
                round(d.recovery_minutes, 1), True,
            ])


def main(argv=None):
    parser = argparse.ArgumentParser(description="SYNTHETIC DORA metrics for HealthQueue")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--json", metavar="PATH", help="save metrics as JSON")
    parser.add_argument("--csv", metavar="PATH", help="save raw synthetic deployments as CSV")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")

    deployments = generate_deployments(args.days, args.seed)
    metrics = calculate_metrics(deployments, args.days)
    print(format_report(metrics))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"synthetic": True, "seed": args.seed, **metrics}, f, indent=2)
    if args.csv:
        write_csv(deployments, args.csv)
    return metrics


if __name__ == "__main__":
    main()
