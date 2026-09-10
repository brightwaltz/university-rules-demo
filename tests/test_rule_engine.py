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
    student = eligible_student.model_copy(update={"completed_courses": ["REQ-A"]})
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


def test_decision_carries_ontology_terms_and_horn_clause(engine, eligible_student):
    result = engine.evaluate_graduation(eligible_student)
    assert "schema:numberOfCredits" in result.ontology_terms
    assert "ccso:hasCompleted" in result.ontology_terms
    assert result.horn_clause is not None
    assert "eligible_to_graduate" in result.horn_clause


def test_premises_record_the_failing_atom(engine, eligible_student):
    student = eligible_student.model_copy(update={"earned_credits": 100})
    result = engine.evaluate_graduation(student)
    credit_premise = next(p for p in result.premises if p.term == "urd:earnedCredits")
    assert credit_premise.actual == 100
    assert credit_premise.expected == 124
    assert credit_premise.comparator == ">="
    assert credit_premise.satisfied is False


def test_rule_reference_cites_the_legislation_article(engine, eligible_student):
    result = engine.get_registration_deadline()
    reference = result.rule_references[0]
    assert reference.legislation_iri == "urdi:legislation/risyu-kitei-2026/art5"
    assert reference.legislation_identifier == "2026年度履修規程 第5条"
