"""例題チェック：担当者が確認した例題は、システムの判定と照合する回帰テストになる。"""

import importlib.util
import shutil
import warnings
from pathlib import Path

import pytest

from src.examples import (
    ExampleRepository,
    ExampleValidationError,
    build_example_graph,
    describe_student,
    dump_examples,
    effective_expected,
    evaluate_example,
    is_reviewed,
    matches,
)
from src.knowledge_graph import KnowledgeGraph

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_PATH = ROOT / "data" / "examples.yaml"
RULES_PATH = ROOT / "data" / "rules.yaml"

# AI が作った例題のうち、システムの判定（not_eligible）と食い違うもの。
# どちらも AI は「このルールで判定すべきでない（unknown）」と考えた例で、規程の解釈を担当者が決める。
AI_MISMATCHES = {"FY-AI-02", "FY-AI-03"}


def _generator():
    spec = importlib.util.spec_from_file_location(
        "generate_examples", ROOT / "scripts" / "generate_examples.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def base_graph() -> KnowledgeGraph:
    return KnowledgeGraph(ROOT / "data" / "university_graph.jsonld")


@pytest.fixture(scope="module")
def examples(base_graph) -> list[dict]:
    return ExampleRepository(EXAMPLES_PATH, base_graph).load()


@pytest.fixture(scope="module")
def outcomes(examples, base_graph) -> dict:
    return {example["id"]: evaluate_example(example, RULES_PATH, base_graph) for example in examples}


def test_all_examples_load_and_validate(examples, base_graph):
    counts: dict[tuple[str, str], int] = {}
    for example in examples:
        key = (example["rule_type"], example["origin"])
        counts[key] = counts.get(key, 0) + 1
        build_example_graph(example, base_graph)  # 修得記録の整合などグラフの検証も通る
    assert counts == {
        ("first_year_credit_requirement", "ai"): 8,
        ("thesis_eligibility", "ai"): 4,
        ("graduation", "ai"): 3,
        ("graduation_required_courses", "ai"): 1,
        ("first_year_credit_requirement", "boundary"): 24,
        ("thesis_eligibility", "boundary"): 24,
        ("graduation", "boundary"): 12,
        ("graduation_required_courses", "boundary"): 4,
    }
    # 並びは ai/human → boundary
    origins = [example["origin"] for example in examples]
    assert origins == sorted(origins, key=lambda origin: origin == "boundary")


def test_reviewed_examples_match_the_system(examples, outcomes):
    """担当者が agree / disagree とした例題は、システムの判定と一致しなければならない。"""
    failures = [
        f"{example['id']}: 期待 {effective_expected(example)} / システム {outcomes[example['id']].status}"
        f"（担当者のメモ: {(example.get('review') or {}).get('note')}）"
        for example in examples
        if (example.get("review") or {}).get("verdict") in {"agree", "disagree"}
        and not matches(example, outcomes[example["id"]])
    ]
    assert not failures, "担当者が確認した例題とシステムの判定が食い違っています:\n" + "\n".join(failures)


def test_unreviewed_mismatches_are_reported(examples, outcomes):
    """未確認で不一致の例題は失敗にしないが、件数を出す（-rA で見える）。"""
    mismatched = [
        example["id"]
        for example in examples
        if not is_reviewed(example) and not matches(example, outcomes[example["id"]])
    ]
    message = f"未確認で、システムの判定と一致しない例題: {len(mismatched)}件 {mismatched}"
    print(message)
    if mismatched:
        warnings.warn(message, stacklevel=1)


def test_only_the_known_ai_examples_disagree_with_the_system(examples, outcomes):
    mismatched = {
        example["id"] for example in examples if example["expected"]["status"] != outcomes[example["id"]].status
    }
    assert mismatched == AI_MISMATCHES
    for example_id in AI_MISMATCHES:
        example = next(item for item in examples if item["id"] == example_id)
        assert example["expected"]["status"] == "unknown"
        assert outcomes[example_id].status == "not_eligible"


def test_outcome_carries_the_engine_proof(examples, outcomes):
    outcome = outcomes["FY-AI-06"]
    assert outcome.status == "not_eligible"
    assert outcome.unmet == ["1年次の修得単位が1単位不足"]
    assert outcome.proof["goal"] == "meets_first_year_requirement(urdi:example/FY-AI-06/student)"
    assert outcome.proof["proved"] is False
    assert "reading" in outcome.proof


def test_generate_examples_check_passes():
    assert _generator().main(["--check"]) == 0


def test_example_graph_projects_the_profile(base_graph):
    example = {
        "id": "X-1",
        "student": {
            "year_of_study": 4,
            "history": [
                {"academic_year": 2023, "year_of_study": 1, "courses": ["REQ-A"], "other_credits": 27},
                {"academic_year": 2024, "year_of_study": 2, "courses": ["REQ-B"]},
            ],
            "registration": {"courses": ["FND-01"], "other_credits": 10, "completed": False},
        },
    }
    graph = build_example_graph(example, base_graph)
    student = next(item for item in graph.students() if item.iri == "urdi:example/X-1/student")
    assert student.earned_credits == 35
    assert student.completed_courses[:2] == ["REQ-A", "X-1-2023"]
    assert student.current_registered_credits == base_graph.course("FND-01").credits + 10
    assert student.registration_completed is False
    assert [record["credits"] for record in graph.completion_records(student.iri)] == [31, 4]
    # 履修登録なし・修得の記録なし
    empty = build_example_graph({"id": "X-2", "student": {"year_of_study": 3, "history": []}}, base_graph)
    node = empty.node("urdi:example/X-2/student")
    assert "ccso:hasCompleted" not in node and "ccso:hasRegistered" not in node
    assert empty.nodes_of_type("schema:AchieveAction") == base_graph.nodes_of_type("schema:AchieveAction")


def test_describe_student_uses_business_words(examples, base_graph):
    by_id = {example["id"]: example for example in examples}
    assert describe_student(by_id["FY-AI-05"], base_graph) == (
        "現在2年生。2025年度（1年次）に31単位を修得（必修Aを含む）。合計31単位。"
    )
    assert describe_student(by_id["FY-AI-02"], base_graph) == "現在3年生（編入学）。本学での修得の記録はない。"
    for example in examples:
        text = describe_student(example, base_graph)
        assert "urdi:" not in text and "REQ-" not in text and "GEN-" not in text, example["id"]


# --- 仕組みのテスト ----------------------------------------------------------


@pytest.fixture
def editable_examples(tmp_path) -> Path:
    path = tmp_path / "examples.yaml"
    shutil.copy(EXAMPLES_PATH, path)
    return path


def test_disagree_review_is_detected_as_mismatch(editable_examples, base_graph):
    repository = ExampleRepository(editable_examples, base_graph)
    repository.save_reviews(
        {
            "FY-AI-06": {
                "verdict": "disagree",
                "correct_status": "eligible",
                "note": "仮の誤り",
                "reviewed_on": "2026-10-09",
            }
        }
    )
    examples = repository.load()
    example = next(item for item in examples if item["id"] == "FY-AI-06")
    assert effective_expected(example) == "eligible"
    assert not matches(example, evaluate_example(example, RULES_PATH, base_graph))
    # 保存は共通の書き出し関数を通る（先頭のコメントを保つ）
    text = editable_examples.read_text(encoding="utf-8")
    assert text == dump_examples(examples)
    assert text.startswith("# 例題：仮想の学生と、規程から期待される結論の組。")
    # 担当者の review は、境界値を生成し直しても保たれる
    generator = _generator()
    assert generator.main(["--check", "--path", str(editable_examples)]) == 0


def test_boundary_review_survives_regeneration(editable_examples, base_graph):
    repository = ExampleRepository(editable_examples, base_graph)
    repository.save_reviews({"GR-B-005": {"verdict": "agree", "reviewed_on": "2026-10-09"}})
    generator = _generator()
    assert generator.main(["--path", str(editable_examples)]) == 0
    example = next(item for item in repository.load() if item["id"] == "GR-B-005")
    assert example["review"]["verdict"] == "agree"


@pytest.mark.parametrize(
    ("review", "message"),
    [
        ({"verdict": "disagree"}, "正しい結論"),
        ({"verdict": "agree", "correct_status": "eligible"}, "correct_status"),
        ({"verdict": "maybe"}, "verdict"),
    ],
)
def test_inconsistent_review_is_rejected_without_writing(editable_examples, base_graph, review, message):
    before = editable_examples.read_text(encoding="utf-8")
    with pytest.raises(ExampleValidationError, match=message):
        ExampleRepository(editable_examples, base_graph).save_reviews({"FY-AI-01": review})
    assert editable_examples.read_text(encoding="utf-8") == before


def test_validation_rejects_unknown_courses_and_duplicate_ids(base_graph, examples):
    repository = ExampleRepository(EXAMPLES_PATH, base_graph)
    with pytest.raises(ExampleValidationError, match="重複"):
        repository.validate([examples[0], examples[0]])
    broken = {**examples[0], "student": {"year_of_study": 2, "history": [
        {"academic_year": 2025, "year_of_study": 1, "courses": ["NO-SUCH"]}
    ]}}
    with pytest.raises(ExampleValidationError, match="NO-SUCH"):
        repository.validate([broken])
    with pytest.raises(ExampleValidationError, match="rule_type"):
        repository.validate([{**examples[0], "rule_type": "registration_deadline"}])
    with pytest.raises(ExampleValidationError, match="admission"):
        repository.validate([{**examples[0], "student": {**examples[0]["student"], "admission": "exchange"}}])
