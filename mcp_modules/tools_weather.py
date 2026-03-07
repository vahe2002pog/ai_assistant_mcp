import requests
from .mcp_core import mcp

# Словарь для перевода метеорологических кодов WMO в понятный текст
WEATHER_CODES = {
    0: "Ясно ☀️",
    1: "В основном ясно 🌤",
    2: "Переменная облачность ⛅️",
    3: "Пасмурно ☁️",
    45: "Туман 🌫",
    48: "Оседающий туман 🌫",
    51: "Слабая морось 🌧",
    53: "Умеренная морось 🌧",
    55: "Сильная морось 🌧",
    61: "Слабый дождь ☔️",
    63: "Умеренный дождь ☔️",
    65: "Сильный дождь ☔️",
    71: "Слабый снег 🌨",
    73: "Умеренный снег 🌨",
    75: "Сильный снег 🌨",
    77: "Снежные зерна ❄️",
    80: "Слабые ливни 🌦",
    81: "Умеренные ливни 🌦",
    82: "Сильные ливни ⛈",
    85: "Слабый снегопад 🌨",
    86: "Сильный снегопад ❄️",
    95: "Гроза ⛈",
    96: "Гроза со слабым градом ⛈",
    99: "Гроза с сильным градом ⛈"
}

@mcp.tool
def get_weather(city: str) -> str:
    """
    Получает текущую погоду для указанного города.
    
    Используй этот инструмент, чтобы отвечать на вопросы пользователя о погоде.
    
    Args:
        city (str): Название города (например: 'Москва', 'Лондон', 'Tokyo').
        
    Returns:
        str: Сводка о текущей погоде (температура, ощущается как, ветер, влажность).
    """
    try:
        # Шаг 1: Получаем координаты (широту и долготу) города
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=ru&format=json"
        geo_response = requests.get(geo_url, timeout=5)
        geo_response.raise_for_status()
        geo_data = geo_response.json()
        
        if not geo_data.get("results"):
            return f"Город '{city}' не найден. Пожалуйста, уточни название."
            
        location = geo_data["results"][0]
        lat = location["latitude"]
        lon = location["longitude"]
        resolved_city = location.get("name", city)
        country = location.get("country", "")
        
        # Шаг 2: Получаем погоду по координатам
        # current=... — запрашиваем нужные параметры
        # wind_speed_unit=ms — ветер в метрах в секунду
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&current=temperature_2m,"
            f"relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
            f"&wind_speed_unit=ms&timezone=auto"
        )
        
        weather_response = requests.get(weather_url, timeout=5)
        weather_response.raise_for_status()
        weather_data = weather_response.json()
        
        current = weather_data["current"]
        
        # Парсим данные
        temp = current["temperature_2m"]
        feels_like = current["apparent_temperature"]
        humidity = current["relative_humidity_2m"]
        wind_speed = current["wind_speed_10m"]
        wmo_code = current["weather_code"]
        
        condition = WEATHER_CODES.get(wmo_code, "Неизвестное состояние")
        
        # Формируем красивый текстовый ответ для агента
        report = (
            f"Погода в городе {resolved_city} ({country}):\n"
            f"Состояние: {condition}\n"
            f"Температура: {temp}°C (ощущается как {feels_like}°C)\n"
            f"Ветер: {wind_speed} м/с\n"
            f"Влажность: {humidity}%"
        )
        
        return report
        
    except requests.exceptions.Timeout:
        return "Ошибка: Сервис погоды не ответил вовремя. Попробуй позже."
    except requests.exceptions.RequestException as e:
        return f"Ошибка при подключении к сервису погоды: {e}"
    except Exception as e:
        return f"Непредвиденная ошибка при получении погоды: {e}"
