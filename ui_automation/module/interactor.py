from .. import utils

from art import text2art
from typing import Tuple


WELCOME_TEXT = """
Добро пожаловать в Compass🧭 — агент, ориентированный на работу с UI в Windows.
{art}
Пожалуйста, введите ваш запрос для выполнения🛸: """.format(
    art=text2art("Compass")
)


def first_request() -> str:
    """
    Запрашивает первый ввод от пользователя.
    :return: Первоначальный запрос.
    """

    return input()


def new_request() -> Tuple[str, bool]:
    """
    Запрашивает новый запрос у пользователя.
    :return: Кортеж (запрос, завершить_разговор).
    """

    utils.print_with_color(
        """Введите новый запрос. Введите 'N' для выхода.""",
        "cyan",
    )
    request = input()
    if request.upper() == "N":
        complete = True
    else:
        complete = False

    return request, complete


def experience_asker() -> bool:
    """
    Спрашивает, сохранить ли текущий диалог для дальнейшего использования агентом.
    :return: True, если сохранять.
    """
    utils.print_with_color(
        """Хотите сохранить текущий диалог для дальнейшего использования агентом?
[Y] — да, любая другая клавиша — нет.""",
        "magenta",
    )

    ans = input()

    if ans.upper() == "Y":
        return True
    else:
        return False


def question_asker(question: str, index: int) -> str:
    """
    Запрашивает у пользователя ответ на конкретный вопрос.
    :param question: Текст вопроса.
    :param index: Индекс вопроса.
    :return: Ввод пользователя.
    """

    utils.print_with_color(
        """[Вопрос {index}:] {question}""".format(index=index, question=question),
        "cyan",
    )

    return input()


def sensitive_step_asker(action, control_text) -> bool:
    """
    Запрашивает подтверждение для чувствительных действий.
    :param action: Выполняемое действие.
    :param control_text: Текст элемента управления.
    :return: True, если продолжать.
    """

    utils.print_with_color(
        "[Требуется ввод:] Compass🧭 выполнит {action} над элементом [{control_text}]. Подтвердите выполнение (Y/N).".format(
            action=action, control_text=control_text
        ),
        "magenta",
    )

    while True:
        user_input = input().upper()

        if user_input == "Y":
            return True
        elif user_input == "N":
            return False
        else:
            print("Неверный выбор. Введите Y или N. Попробуйте снова.")
