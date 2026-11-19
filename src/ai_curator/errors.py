from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CuratorError(Exception):
    code: str
    user_message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.code


def jira_http_error(status: int) -> CuratorError:
    messages = {
        401: "Jira отклонила токен. Проверьте PAT и повторите подключение.",
        403: "У вашей учётной записи нет доступа к этим данным Jira.",
        404: "Запрошенная запись Jira не найдена или недоступна.",
        429: "Jira временно ограничила частоту запросов. Подождите и повторите.",
    }
    return CuratorError(
        f"JIRA_HTTP_{status}", messages.get(status, "Jira временно недоступна."), status
    )
