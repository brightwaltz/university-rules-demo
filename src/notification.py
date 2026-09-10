from __future__ import annotations

from src.models import DecisionResult, DecisionStatus, Notification, Student
from src.rule_engine import RuleEngine


class NotificationService:
    def __init__(self, rule_engine: RuleEngine):
        self.rule_engine = rule_engine

    def generate(self, student: Student) -> list[Notification]:
        notifications: list[Notification] = []
        try:
            deadline = self.rule_engine.get_registration_deadline()
        except ValueError:
            deadline = None

        if not student.registration_completed and deadline is not None:
            registration_decision = deadline.model_copy(
                update={
                    "message": "履修登録が完了していません。"
                    f"期限は{deadline.facts['deadline']}です。",
                    "facts": {
                        **deadline.facts,
                        "registration_completed": student.registration_completed,
                    },
                }
            )
            notifications.append(
                Notification(
                    notification_id="NOTICE-REG-INCOMPLETE",
                    level="warning",
                    title="履修登録未完了",
                    message=registration_decision.message,
                    decision=registration_decision,
                )
            )

            days_remaining = int(deadline.facts["days_remaining"])
            if 0 <= days_remaining <= 3:
                approaching = deadline.model_copy(
                    update={
                        "message": "履修登録期限が近づいています。"
                        f"あと{days_remaining}日です。",
                        "facts": {
                            **deadline.facts,
                            "registration_completed": student.registration_completed,
                        },
                    }
                )
                notifications.append(
                    Notification(
                        notification_id="NOTICE-REG-APPROACHING",
                        level="error",
                        title="履修登録期限接近",
                        message=approaching.message,
                        decision=approaching,
                    )
                )

        if student.year_of_study >= 4:
            try:
                graduation = self.rule_engine.evaluate_graduation(student)
            except ValueError:
                graduation = None
            if graduation is None:
                return self._append_personalized(notifications, student)
            if graduation.status == DecisionStatus.ELIGIBLE:
                notifications.append(
                    Notification(
                        notification_id="NOTICE-GRAD-ELIGIBLE",
                        level="success",
                        title="卒業要件充足",
                        message=graduation.message,
                        decision=graduation,
                    )
                )
            else:
                detail = "、".join(graduation.unmet_conditions)
                if student.earned_credits < int(graduation.facts["required_credits"]):
                    message = "現在の修得単位数では卒業要件を満たしていません。"
                else:
                    message = "現在の必修科目の修得状況では卒業要件を満たしていません。"
                if detail:
                    message += f" 不足：{detail}。"
                graduation_notice = graduation.model_copy(update={"message": message})
                notifications.append(
                    Notification(
                        notification_id="NOTICE-GRAD-INELIGIBLE",
                        level="warning",
                        title="卒業要件不足",
                        message=message,
                        decision=graduation_notice,
                    )
                )

        return self._append_personalized(notifications, student)

    def _append_personalized(
        self, notifications: list[Notification], student: Student
    ) -> list[Notification]:
        for rule, decision in self.rule_engine.evaluate_personalized_notifications(student):
            notifications.append(
                Notification(
                    notification_id=f"NOTICE-{rule.rule_id}",
                    level=str(getattr(rule, "level", "info")),
                    title=rule.title,
                    message=decision.message,
                    decision=decision,
                )
            )
        return notifications
