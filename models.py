from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class ScreenBlock(BaseModel):
    type: Literal["text", "list", "table", "links", "files"]

    title: Optional[str] = None

    text: Optional[str] = None

    items: Optional[List[str]] = None

    rows: Optional[List[Dict[str, Any]]] = None

    links: Optional[List[str]] = None

    file_paths: Optional[List[str]] = None


class ScreenData(BaseModel):
    blocks: List[ScreenBlock]


class AssistantResponse(BaseModel):
    voice: str = Field(
        description="Короткий ответ для синтеза речи (1 предложение, до 10 слов)"
    )

    screen: Optional[ScreenData] = None
