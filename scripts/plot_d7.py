"""D7 figure: category accuracy of the five predictors in both modes, from the saved runs.

``python scripts/plot_d7.py [--results results] [--out reports/figures/d7-accuracy.png]``

Needs the ``plot`` extra (``pip install -e ".[plot]"``) and the saved run output under results/
(gitignored). Nothing is hard-coded but the run directory names. Each run's accuracy is its correct
cases over the lab's cases. A case with no scored diagnosis (an api_error left in the record, or a
missing line) counts as wrong, the rule the D6b report applied to inj-024. No model is called and no
result file is written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ci_triage.labpaths import RESULTS_DIR
from ci_triage.replay import load_cases, load_lab

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "figures" / "d7-accuracy.png"

# (label, results directory for log-only, for full), in the order of the reports
PREDICTORS = [
    ("rules v1", "rules-log-only", "rules"),
    ("one-call v2, Gemini", "one_call_v2-gemini-log-only", "one_call_v2-gemini"),
    ("one-call v2, Ollama", "one_call_v2-ollama-log-only", "one_call_v2-ollama"),
    ("agent v1, Gemini", "agent_v1-gemini-log-only", "agent_v1-gemini"),
    ("agent v1, Ollama", "agent_v1-ollama-log-only", "agent_v1-ollama"),
]
MODES = ["log-only (headline)", "full"]

# Reference palette, light mode (dataviz skill): categorical slots 1-5 in fixed order, text and axis tokens
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
SURFACE, TEXT, TEXT_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"


def correct_cases(directory: Path) -> set[str]:
    """Case ids the run got right. Rules runs save a CaseResult per line; LLM runs a record with ``result``."""
    correct = set()
    for line in (directory / "cases.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        result = record["result"] if "llm_calls" in record else record
        if result is not None and result["category_correct"]:
            correct.add(record["case_id"])
    return correct


def accuracies(results: Path, case_ids: set[str]) -> dict[str, tuple[int, int]]:
    """label -> (correct in log-only, correct in full), each over len(case_ids)."""
    out = {}
    for label, log_only, full in PREDICTORS:
        out[label] = tuple(len(correct_cases(results / name) & case_ids) for name in (log_only, full))
    return out


def plot(scores: dict[str, tuple[int, int]], n_cases: int, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.0, 4.4), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    width, gap = 0.15, 0.012  # thin bars, a surface gap between neighbours
    for i, (label, values) in enumerate(scores.items()):
        xs = [m + (i - 2) * width for m in range(len(MODES))]
        bars = ax.bar(xs, values, width - gap, color=SERIES[i], label=label, zorder=3)
        for bar, value in zip(bars, values):  # direct labels: three slots are under 3:1 on the surface
            ax.annotate(f"{value}", (bar.get_x() + bar.get_width() / 2, value), xytext=(0, 3),
                        textcoords="offset points", ha="center", va="bottom", fontsize=8, color=TEXT)
    ax.set_xticks(range(len(MODES)), MODES, fontsize=10, color=TEXT)
    ax.set_ylim(0, n_cases * 1.06)  # headroom for the value labels
    ax.set_yticks(range(0, n_cases + 1, 10))
    ax.set_ylabel(f"cases correct (of {n_cases})", fontsize=9, color=TEXT_2)
    ax.tick_params(axis="y", labelsize=8, colors=MUTED, length=0)
    ax.tick_params(axis="x", length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.6, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.set_title(f"Category accuracy on ci-triage-lab v1.0 ({n_cases} cases)", fontsize=11, color=TEXT,
                 loc="left", pad=28)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=5, frameon=False, fontsize=7.5,
              handlelength=1.0, handletextpad=0.4, columnspacing=1.0, labelcolor=TEXT_2)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=SURFACE, metadata={"Software": None})  # no version stamp: reproducible bytes
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--results", type=Path, default=RESULTS_DIR)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    case_ids = {case.case_id for case in load_cases(load_lab())}
    scores = accuracies(args.results, case_ids)
    for label, (log_only, full) in scores.items():
        print(f"{label}: log-only {log_only}/{len(case_ids)}, full {full}/{len(case_ids)}")
    plot(scores, len(case_ids), args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
