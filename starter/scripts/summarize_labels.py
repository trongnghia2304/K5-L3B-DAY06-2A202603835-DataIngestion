"""Aggregate the human labels in results/manual/*.csv into results/manual_summary.md.

Fill the label columns first (0/1/2 or y/n, plus a reason). Rows left blank are
reported as missing, never guessed.  Run:  python starter/scripts/summarize_labels.py
"""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANUAL = ROOT / "results/manual"


def read(name: str) -> list[dict[str, str]]:
    with (MANUAL / name).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    out = ["# Manual audit summary", "", "Người chấm: tác giả (tự chấm, có nguy cơ thiên lệch).", ""]

    # Coherence: one score per (position, strategy); if a position is covered by
    # two overlapping chunks, the position score is the mean of those chunk labels.
    rows = read("coherence_labels.csv")
    per = defaultdict(list)
    missing = 0
    for r in rows:
        v = r["label_0_1_2"].strip()
        if v == "":
            missing += 1
            continue
        if v not in {"0", "1", "2"}:
            raise SystemExit(f"coherence: invalid label {v!r} for {r['position_id']} {r['strategy']}")
        per[(r["strategy"], r["position_id"])].append(int(v))
    out += ["## Chunk coherence (10 vị trí, thang 0–2)", "",
            "| Strategy | Vị trí đã chấm | Trung bình /2 |", "|---|---|---|"]
    for s in sorted({r["strategy"] for r in rows}):
        scores = [sum(v) / len(v) for (st, _), v in per.items() if st == s]
        avg = f"{sum(scores) / len(scores):.2f}" if scores else "—"
        out.append(f"| {s} | {len(scores)}/10 | {avg} |")
    out += ["", f"Dòng chưa có nhãn: {missing}", ""]

    # Metadata: records complete and correct / records checked; filter compliance.
    rows = read("metadata_audit.csv")
    out += ["## Metadata usefulness (5 câu test × top-3)", "",
            "| Strategy | Đủ và đúng (n/N) | Filter tuân thủ (câu có filter) |", "|---|---|---|"]
    missing = 0
    for s in sorted({r["strategy"] for r in rows}):
        rs = [r for r in rows if r["strategy"] == s]
        labeled = [r for r in rs if r["metadata_complete_and_correct_y_n"].strip().lower() in {"y", "n"}]
        missing += len(rs) - len(labeled)
        ok = sum(r["metadata_complete_and_correct_y_n"].strip().lower() == "y" for r in labeled)
        fq = defaultdict(list)
        for r in rs:
            if r["question_filter"]:
                f = json.loads(r["question_filter"])
                fq[r["question_id"]].append(all(r.get(k) == v for k, v in f.items()))
        comp = f"{sum(all(v) for v in fq.values())}/{len(fq)}" if fq else "N/A"
        out.append(f"| {s} | {ok}/{len(labeled)} | {comp} |")
    out += ["", f"Bản ghi chưa có nhãn: {missing}", ""]

    # Grounding: distribution of 0/1/2 and mean per strategy.
    rows = read("grounding_labels.csv")
    out += ["## Grounding quality (5 câu test, thang 0–2)", "",
            "| Strategy | 0 | 1 | 2 | Trung bình /2 | Evidence Hit@3 tự động (proxy) |", "|---|---|---|---|---|---|"]
    missing = 0
    for s in sorted({r["strategy"] for r in rows}):
        rs = [r for r in rows if r["strategy"] == s]
        vals = [int(r["label_0_1_2"]) for r in rs if r["label_0_1_2"].strip() in {"0", "1", "2"}]
        missing += len(rs) - len(vals)
        c = Counter(vals)
        avg = f"{sum(vals) / len(vals):.2f}" if vals else "—"
        proxy = sum(r["auto_evidence_hit_at_3"] == "True" for r in rs)
        out.append(f"| {s} | {c[0]} | {c[1]} | {c[2]} | {avg} | {proxy}/{len(rs)} |")
    out += ["", f"Dòng chưa có nhãn: {missing}", ""]

    path = ROOT / "results/manual_summary.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
