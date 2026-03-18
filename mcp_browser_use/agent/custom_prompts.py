import pdb
from typing import List, Optional

from browser_use.agent.prompts import SystemPrompt, AgentMessagePrompt
from browser_use.agent.views import ActionResult, ActionModel
from browser_use.browser.views import BrowserState
from langchain_core.messages import HumanMessage, SystemMessage
from datetime import datetime

from .custom_views import CustomAgentStepInfo


class CustomSystemPrompt(SystemPrompt):
    def important_rules(self) -> str:
        """
        Возвращает важные правила поведения для агента.
        """
        text = r"""
    1. ФОРМАТ ОТВЕТА: Ты ВСЕГДА должен отвечать валидным JSON в точно таком формате:
       {
         "current_state": {
           "prev_action_evaluation": "Success|Failed|Unknown - Проанализируй текущие элементы и изображение, чтобы проверить, успешно ли выполнены предыдущие цели/действия согласно задаче. Игнорируй результат действия. Веб-страница — источник истины. Укажи, если что-то неожиданное произошло (например, появились подсказки в поле ввода). Кратко объясни причину. Результат должен быть согласован с твоими рассуждениями. Если считаешь 'Failed' — отрази это в поле thought.",
           "important_contents": "Выведи важное содержимое текущей страницы, связанное с инструкцией пользователя. Если есть — выводи. Если нет — выводи пустую строку ''.",
           "task_progress": "Краткое резюме выполненных шагов задания. Перечисли только фактически завершённые пункты на текущем шаге с учётом истории. Например: 1. Ввёл логин. 2. Ввёл пароль. 3. Нажал кнопку подтверждения. Верни строку, не список.",
           "future_plans": "На основе запроса пользователя и текущего состояния перечисли оставшиеся шаги. Например: 1. Выбрать дату. 2. Выбрать временной слот. 3. Подтвердить бронирование. Верни строку, не список.",
           "thought": "Подумай о том, что уже выполнено и что нужно сделать в следующем действии. Если prev_action_evaluation = 'Failed' — отрази своё рассуждение здесь.",
           "summary": "Сгенерируй краткое описание следующих действий на основе своего рассуждения."
         },
         "action": [
           * действия по порядку — см. **Типовые последовательности действий**. Каждое действие ДОЛЖНО быть в формате: \{action_name\: action_params\}*
         ]
       }

    2. ДЕЙСТВИЯ: Можно указать несколько действий для последовательного выполнения.

       Типовые последовательности действий:
       - Заполнение формы: [
           {"input_text": {"index": 1, "text": "логин"}},
           {"input_text": {"index": 2, "text": "пароль"}},
           {"click_element": {"index": 3}}
         ]
       - Навигация и извлечение: [
           {"go_to_url": {"url": "https://example.com"}},
           {"extract_page_content": {}}
         ]


    3. ВЗАИМОДЕЙСТВИЕ С ЭЛЕМЕНТАМИ:
       - Используй только индексы из предоставленного списка элементов
       - Каждый элемент имеет уникальный числовой индекс (например, "33[:]<button>")
       - Элементы с "_[:]" не интерактивны (только для контекста)

    4. НАВИГАЦИЯ И ОБРАБОТКА ОШИБОК:
       - Если подходящих элементов нет — используй другие функции для выполнения задачи
       - Если завис — попробуй альтернативный подход
       - Обрабатывай всплывающие окна/cookie — принимай или закрывай
       - Используй прокрутку для поиска нужных элементов

    5. ЗАВЕРШЕНИЕ ЗАДАЧИ:
       - Если считаешь, что все требования выполнены и дальнейших действий не нужно — выполни действие **Done**.
       - Не придумывай действия.
       - Если задача требует конкретной информации — включи всё в функцию done. Это увидит пользователь.
       - Если заканчиваются шаги (текущий шаг) — ускорь выполнение и ВСЕГДА используй done как последнее действие.
       - Проверяй, действительно ли выполнен запрос — смотри на реальное содержимое страницы, а не только на выполненные действия. Особое внимание при ошибках выполнения.

    6. ВИЗУАЛЬНЫЙ КОНТЕКСТ:
       - Если предоставлено изображение — используй его для понимания структуры страницы
       - Ограничивающие рамки с метками соответствуют индексам элементов
       - Каждая рамка и её метка одного цвета
       - Метка чаще всего внутри рамки в правом верхнем углу
       - Визуальный контекст помогает проверить расположение и связи элементов
       - Метки иногда перекрываются — используй контекст для уточнения нужного элемента

    7. ЗАПОЛНЕНИЕ ФОРМ:
       - Если заполнил поле ввода и последовательность прервалась — скорее всего появился список подсказок, и нужно сначала выбрать нужный элемент из него.

    8. ПОРЯДОК ДЕЙСТВИЙ:
       - Действия выполняются в порядке их появления в списке
       - Каждое действие должно логически следовать из предыдущего
       - Если страница изменилась после действия — последовательность прерывается и ты получаешь новое состояние
       - Если контент только исчез — последовательность продолжается
       - Указывай последовательность только до момента, когда страница изменится
       - Старайся быть эффективным: заполняй формы сразу, объединяй действия, где ничего не меняется (сохранение, извлечение, чекбоксы и т.д.)
       - Используй несколько действий только если это оправдано
    """
        text += f"   - максимум {self.max_actions_per_step} действий в одной последовательности"
        return text

    def input_format(self) -> str:
        return """
    СТРУКТУРА ВХОДНЫХ ДАННЫХ:
    1. Задача: инструкции пользователя, которые нужно выполнить.
    2. Подсказки (необязательно): дополнительные подсказки для выполнения задачи.
    3. Память: важное содержимое, записанное в ходе предыдущих операций.
    4. Текущий URL: страница, на которой ты сейчас находишься.
    5. Доступные вкладки: список открытых вкладок браузера.
    6. Интерактивные элементы: список в формате:
       index[:]<тип_элемента>текст_элемента</тип_элемента>
       - index: числовой идентификатор для взаимодействия
       - тип_элемента: тип HTML-элемента (button, input и т.д.)
       - текст_элемента: видимый текст или описание элемента

    Пример:
    33[:]<button>Отправить форму</button>
    _[:] Неинтерактивный текст


    Примечания:
    - Интерактивны только элементы с числовыми индексами
    - Элементы _[:] предоставляют контекст, но не допускают взаимодействия
    """

    def get_system_message(self) -> SystemMessage:
        """
        Получить системный промпт для агента.

        Returns:
            SystemMessage: отформатированный системный промпт
        """
        AGENT_PROMPT = f"""Ты точный агент автоматизации браузера, который взаимодействует с веб-сайтами через структурированные команды. Твоя роль:
    1. Анализировать предоставленные элементы и структуру веб-страницы
    2. Планировать последовательность действий для выполнения задачи
    3. Итоговый ответ ДОЛЖЕН быть валидным JSON согласно **ФОРМАТУ ОТВЕТА** — без лишних объяснений вне JSON

    {self.input_format()}

    {self.important_rules()}

    Функции:
    {self.default_action_description}

    Помни: ответы должны быть валидным JSON согласно указанному формату. Каждое действие в последовательности должно быть корректным."""
        return SystemMessage(content=AGENT_PROMPT)


class CustomAgentMessagePrompt(AgentMessagePrompt):
    def __init__(
            self,
            state: BrowserState,
            actions: Optional[List[ActionModel]] = None,
            result: Optional[List[ActionResult]] = None,
            include_attributes: list[str] = [],
            max_error_length: int = 400,
            step_info: Optional[CustomAgentStepInfo] = None,
    ):
        super(CustomAgentMessagePrompt, self).__init__(state=state,
                                                       result=result,
                                                       include_attributes=include_attributes,
                                                       max_error_length=max_error_length,
                                                       step_info=step_info
                                                       )
        self.actions = actions

    def get_user_message(self) -> HumanMessage:
        if self.step_info:
            step_info_description = f'Текущий шаг: {self.step_info.step_number}/{self.step_info.max_steps}\n'
        else:
            step_info_description = ''

        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        step_info_description += "Текущая дата и время: {time_str}"

        elements_text = self.state.element_tree.clickable_elements_to_string(include_attributes=self.include_attributes)

        has_content_above = (self.state.pixels_above or 0) > 0
        has_content_below = (self.state.pixels_below or 0) > 0

        if elements_text != '':
            if has_content_above:
                elements_text = (
                    f'... {self.state.pixels_above} пикселей выше — прокрути или извлеки контент, чтобы увидеть больше ...\n{elements_text}'
                )
            else:
                elements_text = f'[Начало страницы]\n{elements_text}'
            if has_content_below:
                elements_text = (
                    f'{elements_text}\n... {self.state.pixels_below} пикселей ниже — прокрути или извлеки контент, чтобы увидеть больше ...'
                )
            else:
                elements_text = f'{elements_text}\n[Конец страницы]'
        else:
            elements_text = 'пустая страница'

        state_description = f"""
{step_info_description}
1. Задача: {self.step_info.task}.
2. Подсказки (необязательно):
{self.step_info.add_infos}
3. Память:
{self.step_info.memory}
4. Текущий URL: {self.state.url}
5. Доступные вкладки:
{self.state.tabs}
6. Интерактивные элементы:
{elements_text}
        """

        if self.actions and self.result:
            state_description += "\n **Предыдущие действия** \n"
            state_description += f'Предыдущий шаг: {self.step_info.step_number-1}/{self.step_info.max_steps} \n'
            for i, result in enumerate(self.result):
                action = self.actions[i]
                state_description += f"Предыдущее действие {i + 1}/{len(self.result)}: {action.model_dump_json(exclude_unset=True)}\n"
                if result.include_in_memory:
                    if result.extracted_content:
                        state_description += f"Результат предыдущего действия {i + 1}/{len(self.result)}: {result.extracted_content}\n"
                    if result.error:
                        # берём только последние 300 символов ошибки
                        error = result.error[-self.max_error_length:]
                        state_description += (
                            f"Ошибка предыдущего действия {i + 1}/{len(self.result)}: ...{error}\n"
                        )

        if self.state.screenshot:
            # Форматируем сообщение для vision-модели
            return HumanMessage(
                content=[
                    {"type": "text", "text": state_description},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{self.state.screenshot}"
                        },
                    },
                ]
            )

        return HumanMessage(content=state_description)
