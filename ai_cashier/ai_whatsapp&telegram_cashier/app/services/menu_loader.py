import sys
import json
from pathlib import Path
from app.models.menu import Menu

class MenuLoader : 
    def __init__(self ,menu_path : str | Path):
        self.menu_path = Path(menu_path)

    def load(self) -> Menu : 
        with self.menu_path.open('r',encoding="utf-8") as f :
            data = json.load(f)

        return Menu.model_validate(data)