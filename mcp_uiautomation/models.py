# -*- coding: utf-8 -*-
"""Модели данных для UIAutomation MCP Server."""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


class ControlInfo(BaseModel):
    """Информация о контроле, возвращаемая инструментами обнаружения."""
    handle: int = Field(..., description="Дескриптор окна (для последующих операций)")
    name: str = Field("", description="Название контрола")
    class_name: str = Field("", alias="className", description="Класс окна Windows")
    control_type: str = Field("", alias="controlType", description="Тип контрола")
    automation_id: str = Field("", alias="automationId", description="Automation ID")
    process_id: int = Field(0, alias="processId", description="ID процесса")
    enabled: bool = Field(True, description="Доступен ли контрол")
    visible: bool = Field(True, description="Виден ли контрол")
    rect: Optional[Dict[str, int]] = Field(None, description="Ограничивающий прямоугольник")

    class Config:
        populate_by_name = True


class ControlSelector(BaseModel):
    """Селектор для поиска контролов."""
    parent_handle: Optional[int] = Field(None, alias="parentHandle", description="Дескриптор родителя")
    control_type: Optional[str] = Field(None, alias="controlType", description="Тип контрола для поиска")
    name: Optional[str] = Field(None, description="Точное совпадение имени")
    name_contains: Optional[str] = Field(None, alias="nameContains", description="Имя содержит")
    name_regex: Optional[str] = Field(None, alias="nameRegex", description="Регулярное выражение для имени")
    class_name: Optional[str] = Field(None, alias="className", description="Класс окна Windows")
    automation_id: Optional[str] = Field(None, alias="automationId", description="Automation ID")
    depth: int = Field(0xFFFFFFFF, description="Глубина поиска")
    index: int = Field(1, description="N-й подходящий контрол (1-based)")

    class Config:
        populate_by_name = True


class MouseButton(str, Enum):
    """Типы кнопок мыши."""
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ScrollDirection(str, Enum):
    """Направления прокрутки."""
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class ExpandCollapseAction(str, Enum):
    """Действия для Expand/Collapse."""
    EXPAND = "expand"
    COLLAPSE = "collapse"


class ConfirmationRequest(BaseModel):
    """Запрос подтверждения для опасных операций."""
    type: str = "confirmation_required"
    tool: str
    details: Dict[str, Any]
    message: str
    confirmation_token: str = Field(..., alias="confirmationToken")

    class Config:
        populate_by_name = True


class ErrorResponse(BaseModel):
    """Формат ответа об ошибке."""
    success: bool = False
    error: Dict[str, Any]


class SuccessResponse(BaseModel):
    """Формат успешного ответа."""
    success: bool = True
    data: Optional[Any] = None
