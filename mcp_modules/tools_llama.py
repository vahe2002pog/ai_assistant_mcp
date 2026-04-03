import requests
from .mcp_core import mcp
from config import LLAMA_BASE_URL


@mcp.tool
def llama_chat(prompt: str, system: str = "", max_tokens: int = 1024, temperature: float = 0.7) -> str:
    """
    Отправляет запрос к локальному llama.cpp серверу и возвращает ответ модели.
    Модель не указывается — используется та, что запущена на сервере.

    Args:
        prompt (str): Сообщение пользователя.
        system (str): Системный промпт (необязательно).
        max_tokens (int): Максимальное количество токенов в ответе (по умолчанию 1024).
        temperature (float): Температура генерации (по умолчанию 0.7).

    Returns:
        str: Ответ модели.
    """
    print(f"Вызван llama_chat с prompt: {prompt[:80]}...")
    try:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = requests.post(
            f"{LLAMA_BASE_URL}/v1/chat/completions",
            json={
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.ConnectionError:
        return f"Ошибка: не удалось подключиться к llama.cpp по адресу {LLAMA_BASE_URL}. Убедитесь, что сервер запущен."
    except Exception as e:
        return f"Ошибка llama_chat: {e}"


@mcp.tool
def llama_complete(prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str:
    """
    Простое завершение текста через llama.cpp (completion endpoint).
    Модель не указывается — используется та, что запущена на сервере.

    Args:
        prompt (str): Текст для продолжения.
        max_tokens (int): Максимальное количество токенов (по умолчанию 512).
        temperature (float): Температура генерации (по умолчанию 0.7).

    Returns:
        str: Сгенерированное продолжение текста.
    """
    print(f"Вызван llama_complete с prompt: {prompt[:80]}...")
    try:
        response = requests.post(
            f"{LLAMA_BASE_URL}/completion",
            json={
                "prompt": prompt,
                "n_predict": max_tokens,
                "temperature": temperature,
            },
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("content", "")
    except requests.exceptions.ConnectionError:
        return f"Ошибка: не удалось подключиться к llama.cpp по адресу {LLAMA_BASE_URL}. Убедитесь, что сервер запущен."
    except Exception as e:
        return f"Ошибка llama_complete: {e}"
