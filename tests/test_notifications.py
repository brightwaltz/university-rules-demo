from src.notification import NotificationService


def test_incomplete_registration_creates_notification(engine, eligible_student):
    student = eligible_student.model_copy(update={"registration_completed": False})
    notices = NotificationService(engine).generate(student)
    ids = {notice.notification_id for notice in notices}
    assert "NOTICE-REG-INCOMPLETE" in ids
    assert "NOTICE-REG-APPROACHING" in ids


def test_completed_registration_does_not_create_incomplete_notice(engine, eligible_student):
    notices = NotificationService(engine).generate(eligible_student)
    ids = {notice.notification_id for notice in notices}
    assert "NOTICE-REG-INCOMPLETE" not in ids
    assert "NOTICE-REG-APPROACHING" not in ids


def test_fourth_year_with_credit_shortage_gets_graduation_notice(engine, eligible_student):
    student = eligible_student.model_copy(update={"earned_credits": 118})
    notices = NotificationService(engine).generate(student)
    notice = next(item for item in notices if item.notification_id == "NOTICE-GRAD-INELIGIBLE")
    assert "現在の修得単位数では卒業要件を満たしていません。" in notice.message
    assert "6単位不足" in notice.message
