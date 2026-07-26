"""AtCoder_100_json AC 코드 → 문제 클러스터링용 기준선 CSV (dev-plan §5.1).

각 문제의 AC 코드를 샘플링해 구조 패턴 매처(§4.1 Step 5)와 구조 통계를 적용,
문제별 패턴 출현 빈도 벡터를 만든다. 이 벡터가 추후 k-means 클러스터링 입력.

실행: backend 디렉토리에서  python -m scripts.extract_baseline
출력:
  data/baseline_submission_patterns.csv  (제출 1건당 1행)
  data/baseline_problem_pattern_freq.csv (문제당 1행 — 클러스터링 입력 벡터)
"""

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from app.worker import MATCHER_VERSION
from app.worker.astsupport import load_parser
from app.worker.patterns import PATTERNS, match_patterns, structure_stats

JSON_ROOT = Path(__file__).resolve().parents[2] / "AtCoder_100_json"
OUT_DIR = Path(__file__).resolve().parents[1] / "data"

LANGS = ("cpp", "python")       # 패턴 매처 지원 언어만
SAMPLE_PER_LANG = 30            # 문제당·언어당 AC 샘플 수 (결정론: 파일명 정렬 순)

STAT_KEYS = ["loop_count", "max_loop_depth", "has_recursion", "function_count", "subscript_count"]


def iter_samples(problem_dir: Path, lang: str):
    """파일명 정렬 순으로 AC 제출을 SAMPLE_PER_LANG개까지 yield."""
    count = 0
    for f in sorted(problem_dir.glob(f"*__{lang}__*.json")):
        if count >= SAMPLE_PER_LANG:
            break
        try:
            sub = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if sub.get("verdict") != "AC" or not sub.get("code"):
            continue
        count += 1
        yield sub


def main() -> int:
    if not JSON_ROOT.exists():
        print(f"not found: {JSON_ROOT}", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(exist_ok=True)

    parsers = {lang: load_parser(lang) for lang in LANGS}
    if any(p is None for p in parsers.values()):
        print("tree-sitter parser missing", file=sys.stderr)
        return 1

    sub_rows = []
    # 문제별 집계: 패턴 출현 횟수 + 구조 통계 합
    prob_counts = defaultdict(lambda: {"n": 0, **{p: 0 for p in PATTERNS}, **{k: 0 for k in STAT_KEYS}})

    problem_dirs = sorted(d for d in JSON_ROOT.iterdir() if d.is_dir())
    for pi, pdir in enumerate(problem_dirs):
        problem_id = pdir.name
        for lang in LANGS:
            for sub in iter_samples(pdir, lang):
                code = sub["code"]
                src = code.encode("utf-8")
                tree = parsers[lang].parse(src)
                matched = {m.pattern for m in match_patterns(tree, src, lang)}
                stats = structure_stats(tree, src, lang)

                row = {
                    "problem_id": problem_id,
                    "submission_id": sub.get("submission_id", ""),
                    "language": lang,
                    "code_bytes": len(src),
                    **{p: int(p in matched) for p in PATTERNS},
                    **{k: int(stats.get(k, 0)) for k in STAT_KEYS},
                }
                sub_rows.append(row)

                agg = prob_counts[problem_id]
                agg["n"] += 1
                for p in matched:
                    agg[p] += 1
                for k in STAT_KEYS:
                    agg[k] += int(stats.get(k, 0))
        print(f"[{pi + 1}/{len(problem_dirs)}] {problem_id} done")

    # ---- 제출 단위 CSV ----
    sub_path = OUT_DIR / "baseline_submission_patterns.csv"
    with open(sub_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["problem_id", "submission_id", "language", "code_bytes"]
            + PATTERNS + STAT_KEYS,
        )
        writer.writeheader()
        writer.writerows(sub_rows)

    # ---- 문제 단위 빈도 벡터 CSV (클러스터링 입력) ----
    prob_path = OUT_DIR / "baseline_problem_pattern_freq.csv"
    with open(prob_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["problem_id", "sample_count", "matcher_version"]
            + [f"freq_{p}" for p in PATTERNS]
            + [f"avg_{k}" for k in STAT_KEYS]
        )
        for problem_id in sorted(prob_counts):
            agg = prob_counts[problem_id]
            n = agg["n"]
            if n == 0:
                continue
            writer.writerow(
                [problem_id, n, MATCHER_VERSION]
                + [round(agg[p] / n, 4) for p in PATTERNS]
                + [round(agg[k] / n, 4) for k in STAT_KEYS]
            )

    print(f"\nwrote {len(sub_rows)} submissions -> {sub_path}")
    print(f"wrote {len(prob_counts)} problems -> {prob_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
