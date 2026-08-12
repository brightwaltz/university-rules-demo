from src.models import DecisionStatus


def test_remaining_capacity_is_12_for_36_registered(engine, eligible_student):
    student = eligible_student.model_copy(update={"current_registered_credits": 36})
    result = engine.get_remaining_credit_capacity(student)
    assert result.facts["remaining_credits"] == 12
    assert result.rule_ids == ["RULE-CREDIT-001"]


def test_remaining_capacity_is_zero_for_48_registered(engine, eligible_student):
    student = eligible_student.model_copy(update={"current_registered_credits": 48})
    result = engine.get_remaining_credit_capacity(student)
    assert result.facts["remaining_credits"] == 0


def test_graduation_eligible_with_credits_and_required_courses(engine, eligible_student):
    result = engine.evaluate_graduation(eligible_student)
    assert result.status == DecisionStatus.ELIGIBLE
    assert result.unmet_conditions == []


def test_graduation_ineligible_if_required_b_missing(engine, eligible_student):
    student = eligible_student.model_copy(update={"required_b": False})
    result = engine.evaluate_graduation(student)
    assert result.status == DecisionStatus.NOT_ELIGIBLE
    assert "必修Bを未修得" in result.unmet_conditions


def test_thesis_ineligible_below_100_credits(engine, eligible_student):
    student = eligible_student.model_copy(update={"earned_credits": 99})
    result = engine.evaluate_thesis_eligibility(student)
    assert result.status == DecisionStatus.NOT_ELIGIBLE
    assert "修得単位が100単位未満" in result.unmet_conditions


def test_thesis_eligible_when_all_conditions_met(engine, eligible_student):
    result = engine.evaluate_thesis_eligibility(eligible_student)
    assert result.status == DecisionStatus.ELIGIBLE
