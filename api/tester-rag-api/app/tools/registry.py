import os

from app.agents.analyst import CodeAnalyst
from app.agents.widget_tree_extractor import WidgetTreeExtractor
from app.tools.grep import read_file


_analyst = CodeAnalyst()
_widget_extractor = WidgetTreeExtractor()


def get_imports(project_path: str, relative_path: str) -> list[str]:
    abs_path = os.path.join(project_path, relative_path)
    return _analyst.get_imports(abs_path)


def get_widget_tree(project_path: str, relative_path: str) -> list[dict]:
    abs_path = os.path.join(project_path, relative_path)
    return _widget_extractor.extract(abs_path)


def read_project_file(project_path: str, relative_path: str) -> str:
    return read_file(project_path, relative_path)
