"""境界値の例題（origin: boundary）を生成して data/examples.yaml へ合流する。

    python scripts/generate_examples.py          # 生成して書き出す
    python scripts/generate_examples.py --check  # 書き出し結果がファイルと一致するかだけを確かめる

期待結果は Horn プログラムや RuleEngine を使わず、rules.yaml の値（しきい値）と
科目の単位数だけから素朴に計算する（システムの判定とは独立した検算にするため）。
origin が ai / human の例題と、既存の boundary 例題の review（担当者の確認結果）は保持する。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.examples import dump_examples, empty_review, load_examples_document, write_examples  # noqa: E402
from src.knowledge_graph import KnowledgeGraph  # noqa: E402

EXAMPLES_PATH = ROOT / "data" / "examples.yaml"
RULES_PATH = ROOT / "data" / "rules.yaml"
GRAPH_PATH = ROOT / "data" / "university_graph.jsonld"


class Thresholds:
    """rules.yaml から読んだしきい値と、科目の単位数・名称。"""

    def __init__(self, rules_path: Path, graph: KnowledgeGraph):
        raw = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
        rules = {rule["rule_type"]: rule for rule in raw["rules"]}
        evaluation_date = raw["settings"]["evaluation_date"]
        if isinstance(evaluation_date, str):
            evaluation_date = date.fromisoformat(evaluation_date)
        # 4月始まりの年度。基準日の年度が「現在の学年」の年度
        self.current_academic_year = (
            evaluation_date.year if evaluation_date.month >= 4 else evaluation_date.year - 1
        )
        self.required_credits = int(rules["graduation_credit_requirement"]["required_credits"])
        first_year = rules["first_year_credit_requirement"]
        self.target_year = int(first_year["year_of_study"])
        self.ratio = int(first_year["minimum_ratio_percent"])
        self.first_year_minimum = -(-self.required_credits * self.ratio // 100)  # 切り上げ
        thesis = rules["thesis_eligibility"]
        self.thesis_year = int(thesis["minimum_year_of_study"])
        self.thesis_credits = int(thesis["minimum_earned_credits"])
        self.thesis_courses = [str(code) for code in thesis["required_courses"]]
        self.graduation_courses = [
            str(code) for code in rules["graduation_required_courses"]["required_courses"]
        ]
        self.credits = {course.code: course.credits for course in graph.courses()}
        self.names = {course.code: course.name for course in graph.courses()}

    def academic_year(self, current_year: int, year_of_study: int) -> int:
        """現在 current_year 年生の学生が year_of_study 年次だった年度。"""
        return self.current_academic_year - (current_year - year_of_study)


def _example(example_id: str, rule_type: str, title: str, student: dict, status: str, reason: str) -> dict:
    return {
        "id": example_id,
        "rule_type": rule_type,
        "origin": "boundary",
        "title": title,
        "student": student,
        "expected": {"status": status, "reason": reason},
        "ai_note": None,
        "review": empty_review(),
    }


def _course_subsets(codes: list[str]) -> list[list[str]]:
    """必修4通り：なし／1つ目のみ／2つ目のみ／両方。"""
    first, second = codes
    return [[], [first], [second], [first, second]]


def _with_courses(names: list[str]) -> str:
    return f"（{'・'.join(names)}を含む）" if names else "（必修科目を含まない）"


def _missing_courses(t: Thresholds, required: list[str], taken: list[str]) -> list[str]:
    return [t.names[code] for code in required if code not in taken]


def _spread(t: Thresholds, current_year: int, total: int, placed: dict[int, list[str]]) -> list[dict]:
    """total 単位を 1〜(current_year-1) 年次へ配る。placed は年次 → その年次に置く必修科目。

    必修科目の単位を除いた残りを均等に配り、余りは最後の年次に足す。
    """
    years = list(range(1, current_year))
    required_credits = sum(t.credits[code] for codes in placed.values() for code in codes)
    rest = total - required_credits
    base, remainder = divmod(rest, len(years))
    history = []
    for year in years:
        entry: dict[str, Any] = {
            "academic_year": t.academic_year(current_year, year),
            "year_of_study": year,
        }
        if placed.get(year):
            entry["courses"] = list(placed[year])
        entry["other_credits"] = base + (remainder if year == years[-1] else 0)
        history.append(entry)
    return history


def first_year_examples(t: Thresholds) -> list[dict]:
    """FY-B：現在3年生。1年次の修得 × 必修Aの有無 × 2年次の修得。"""
    examples = []
    current_year = 3
    course = t.graduation_courses[0]
    number = 0
    for other in (0, 27, 30, 31, 32, 50):
        for with_course in (False, True):
            for second in (0, 40):
                number += 1
                first = other + (t.credits[course] if with_course else 0)
                entry: dict[str, Any] = {
                    "academic_year": t.academic_year(current_year, t.target_year),
                    "year_of_study": t.target_year,
                }
                if with_course:
                    entry["courses"] = [course]
                entry["other_credits"] = other
                history = [entry]
                if second:
                    history.append(
                        {
                            "academic_year": t.academic_year(current_year, t.target_year + 1),
                            "year_of_study": t.target_year + 1,
                            "other_credits": second,
                        }
                    )
                title = f"1年次に{first}単位" + (f"（{t.names[course]}を含む）" if with_course else "")
                title += f"、2年次に{second}単位" if second else "、2年次の修得なし"
                eligible = first >= t.first_year_minimum
                if eligible:
                    reason = f"1年次の修得{first}単位が、必要な{t.first_year_minimum}単位以上のため"
                else:
                    reason = (
                        f"1年次の修得{first}単位が、必要な{t.first_year_minimum}単位に"
                        f"{t.first_year_minimum - first}単位足りないため"
                    )
                examples.append(
                    _example(
                        f"FY-B-{number:03d}",
                        "first_year_credit_requirement",
                        title,
                        {"year_of_study": current_year, "history": history},
                        "eligible" if eligible else "not_eligible",
                        reason,
                    )
                )
    return examples


def thesis_examples(t: Thresholds) -> list[dict]:
    """TH-B：学年 × 修得単位 × 必修4通り。必修の1つ目は1年次、2つ目は2年次に置く。"""
    examples = []
    number = 0
    for current_year in (t.thesis_year - 1, t.thesis_year):
        for total in (t.thesis_credits - 1, t.thesis_credits, t.thesis_credits + 1):
            for taken in _course_subsets(t.thesis_courses):
                number += 1
                placed = {
                    index + 1: [code] for index, code in enumerate(t.thesis_courses) if code in taken
                }
                history = _spread(t, current_year, total, placed)
                names = [t.names[code] for code in taken]
                missing = _missing_courses(t, t.thesis_courses, taken)
                reasons = []
                if current_year < t.thesis_year:
                    reasons.append(f"{current_year}年生で、第{t.thesis_year}年次以上に在籍していない")
                if total < t.thesis_credits:
                    reasons.append(
                        f"修得{total}単位が、必要な{t.thesis_credits}単位に"
                        f"{t.thesis_credits - total}単位足りない"
                    )
                if missing:
                    reasons.append(f"{'・'.join(missing)}を修得していない")
                if reasons:
                    reason = "、".join(reasons) + "ため"
                else:
                    reason = (
                        f"第{t.thesis_year}年次以上に在籍し、修得{total}単位が必要な{t.thesis_credits}単位以上で、"
                        f"{'・'.join(names)}も修得しているため"
                    )
                examples.append(
                    _example(
                        f"TH-B-{number:03d}",
                        "thesis_eligibility",
                        f"{current_year}年生で{total}単位を修得{_with_courses(names)}",
                        {"year_of_study": current_year, "history": history},
                        "not_eligible" if reasons else "eligible",
                        reason,
                    )
                )
    return examples


def graduation_examples(t: Thresholds) -> list[dict]:
    """GR-B：4年生、修得単位 × 必修4通り。"""
    examples = []
    current_year = 4
    number = 0
    for total in (t.required_credits - 1, t.required_credits, t.required_credits + 1):
        for taken in _course_subsets(t.graduation_courses):
            number += 1
            placed = {
                index + 1: [code] for index, code in enumerate(t.graduation_courses) if code in taken
            }
            history = _spread(t, current_year, total, placed)
            names = [t.names[code] for code in taken]
            missing = _missing_courses(t, t.graduation_courses, taken)
            reasons = []
            if total < t.required_credits:
                reasons.append(
                    f"修得{total}単位が、卒業に必要な{t.required_credits}単位に"
                    f"{t.required_credits - total}単位足りない"
                )
            if missing:
                reasons.append(f"{'・'.join(missing)}を修得していない")
            if reasons:
                reason = "、".join(reasons) + "ため"
            else:
                reason = (
                    f"修得{total}単位が卒業に必要な{t.required_credits}単位以上で、"
                    f"{'・'.join(names)}も修得しているため"
                )
            examples.append(
                _example(
                    f"GR-B-{number:03d}",
                    "graduation",
                    f"4年生で{total}単位を修得{_with_courses(names)}",
                    {"year_of_study": current_year, "history": history},
                    "not_eligible" if reasons else "eligible",
                    reason,
                )
            )
    return examples


def required_course_examples(t: Thresholds) -> list[dict]:
    """RC-B：2年生、1年次にその他30単位 ＋ 必修4通り。"""
    examples = []
    current_year = 2
    for number, taken in enumerate(_course_subsets(t.graduation_courses), start=1):
        entry: dict[str, Any] = {
            "academic_year": t.academic_year(current_year, 1),
            "year_of_study": 1,
        }
        if taken:
            entry["courses"] = list(taken)
        entry["other_credits"] = 30
        names = [t.names[code] for code in taken]
        missing = _missing_courses(t, t.graduation_courses, taken)
        reason = (
            f"{'・'.join(missing)}を修得していないため"
            if missing
            else f"{'・'.join(names)}をすべて修得しているため"
        )
        examples.append(
            _example(
                f"RC-B-{number:03d}",
                "graduation_required_courses",
                f"1年次に{30 + sum(t.credits[code] for code in taken)}単位を修得{_with_courses(names)}",
                {"year_of_study": current_year, "history": [entry]},
                "not_eligible" if missing else "eligible",
                reason,
            )
        )
    return examples


def boundary_examples(rules_path: Path = RULES_PATH, graph_path: Path = GRAPH_PATH) -> list[dict]:
    t = Thresholds(rules_path, KnowledgeGraph(graph_path))
    return [
        *first_year_examples(t),
        *thesis_examples(t),
        *graduation_examples(t),
        *required_course_examples(t),
    ]


def merged_examples(
    existing: list[dict], rules_path: Path = RULES_PATH, graph_path: Path = GRAPH_PATH
) -> list[dict]:
    """ai/human の例題（既存の順）→ 生成した boundary 例題。boundary の review は id で引き継ぐ。"""
    kept = [example for example in existing if example.get("origin") != "boundary"]
    reviews = {
        example["id"]: example.get("review")
        for example in existing
        if example.get("origin") == "boundary" and example.get("review")
    }
    generated = boundary_examples(rules_path, graph_path)
    for example in generated:
        if example["id"] in reviews:
            example["review"] = reviews[example["id"]]
    return [*kept, *generated]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="書き出し結果がファイルと一致するかだけを確かめる"
    )
    parser.add_argument("--path", type=Path, default=EXAMPLES_PATH, help="examples.yaml の場所")
    args = parser.parse_args(argv)

    existing = load_examples_document(args.path) if args.path.exists() else []
    examples = merged_examples(existing)
    if args.check:
        current = args.path.read_text(encoding="utf-8") if args.path.exists() else ""
        if dump_examples(examples) != current:
            print(
                f"{args.path} が生成結果と一致しません。"
                "`python scripts/generate_examples.py` を実行してください。",
                file=sys.stderr,
            )
            return 1
        print(f"{args.path} は生成結果と一致しています（{len(examples)}件）。")
        return 0
    write_examples(args.path, examples)
    print(f"{args.path} に {len(examples)} 件を書き出しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
