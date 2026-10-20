from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Variant(BaseModel):
    size_type: str
    price: float


class MenuItem(BaseModel):
    category: str
    name: str
    # menu.json spells this key "varinats"; the alias keeps the file unchanged.
    variants: list[Variant] = Field(alias="varinats")
    required_choices: Optional[list[str]] = None
    description: Optional[str] = None
    # menu.json uses the correct key "is_available". The previous model declared
    # "is_availble" (typo), so the value in the file was silently ignored.
    is_available: bool = True

    model_config = ConfigDict(populate_by_name=True)

    @property
    def is_availble(self) -> bool:  # backward-compatible alias of the old typo
        return self.is_available


class Menu(BaseModel):
    items: list[MenuItem]
