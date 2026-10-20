from typing import Literal

from pydantic import BaseModel

from app.models.menu import MenuItem


class ProductSearchResult(BaseModel):
    item: MenuItem | None = None
    found: bool
    method: Literal["exact", "alias", "fuzzy", "not_found"]
    score: float | None = None
