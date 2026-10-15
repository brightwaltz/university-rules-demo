from src.jsonld_export import notification_to_jsonld
from src.notification import NotificationService


def test_incomplete_registration_creates_notification(student_on_graph, eligible_profile):
    eligible_profile["registration"]["completed"] = False
    engine, student = student_on_graph(eligible_profile)
    assert student.registration_completed is False
    notices = NotificationService(engine).generate(student)
    ids = {notice.notification_id for notice in notices}
    assert "NOTICE-REG-INCOMPLETE" in ids
    assert "NOTICE-REG-APPROACHING" in ids


def test_completed_registration_does_not_create_incomplete_notice(eligible_case):
    engine, student = eligible_case
    notices = NotificationService(engine).generate(student)
    ids = {notice.notification_id for notice in notices}
    assert "NOTICE-REG-INCOMPLETE" not in ids
    assert "NOTICE-REG-APPROACHING" not in ids


def _student_with_118_credits(student_on_graph, profile):
    profile["history"][2]["other_credits"] = 34  # 40 + 44 + 34 = 118単位（必修A・Bは修得済み）
    engine, student = student_on_graph(profile)
    assert student.earned_credits == 118
    return engine, student


def test_fourth_year_with_credit_shortage_gets_graduation_notice(student_on_graph, eligible_profile):
    engine, student = _student_with_118_credits(student_on_graph, eligible_profile)
    notices = NotificationService(engine).generate(student)
    notice = next(item for item in notices if item.notification_id == "NOTICE-GRAD-INELIGIBLE")
    assert "現在の修得単位数では卒業要件を満たしていません。" in notice.message
    assert "6単位不足" in notice.message


def test_notification_exports_as_schema_org_message(student_on_graph, eligible_profile):
    engine, student = _student_with_118_credits(student_on_graph, eligible_profile)
    notices = NotificationService(engine).generate(student)
    notice = next(item for item in notices if item.notification_id == "NOTICE-GRAD-INELIGIBLE")

    document = notification_to_jsonld(notice, student)
    assert document["@type"] == "schema:Message"
    assert document["schema:recipient"] == {"@id": "urdi:example/TEST-OK/student"}
    assert document["urd:decision"]["urd:status"] == "not_eligible"
    assert document["urd:decision"]["urd:basedOnLegislation"] == [
        {"@id": "urdi:legislation/gakusoku-2026/art32-1"},
        {"@id": "urdi:legislation/gakusoku-2026/art32-2"},
    ]
