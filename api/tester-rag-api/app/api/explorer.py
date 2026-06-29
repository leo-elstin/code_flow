from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os
from app.services.project_explorer import ProjectExplorer
from app.agents.widget_tree_extractor import WidgetTreeExtractor

router = APIRouter()

class ExploreRequest(BaseModel):
    project_path: str
    sub_dir: str = "lib"

@router.post("/explore")
async def explore_project(request: ExploreRequest):
    """
    Scans the project directory and returns a structural JSON tree.
    """
    if not os.path.exists(request.project_path):
        raise HTTPException(status_code=404, detail=f"Project path not found: {request.project_path}")
    
    explorer = ProjectExplorer(request.project_path)
    try:
        tree = explorer.explore(request.sub_dir)
        return {
            "project": os.path.basename(request.project_path),
            "root": request.sub_dir,
            "tree": tree
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Exploration Error: {str(e)}")


class SourceRequest(BaseModel):
    project_path: str
    file_path: str  # relative path within the project, e.g. "lib/src/feature/foo.dart"

@router.post("/source")
async def get_source(request: SourceRequest):
    """
    Returns the raw source content of a single .dart file.
    file_path must be relative to project_path.
    """
    # Resolve and guard against path traversal
    abs_project = os.path.realpath(request.project_path)
    abs_file = os.path.realpath(os.path.join(abs_project, request.file_path))

    if not abs_file.startswith(abs_project):
        raise HTTPException(status_code=400, detail="Path traversal detected.")

    if not os.path.isfile(abs_file):
        raise HTTPException(status_code=404, detail=f"File not found: {request.file_path}")

    try:
        with open(abs_file, "r", encoding="utf-8") as f:
            content = f.read()
        return {
            "project_path": request.project_path,
            "file_path": request.file_path,
            "content": content,
            "lines": content.count("\n") + 1,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Read error: {str(e)}")


class WidgetTreeRequest(BaseModel):
    project_path: str
    file_path: str  # relative path, e.g. "lib/src/feature/invoice/invoice_list_page.dart"


@router.post("/widget-tree")
async def get_widget_tree(request: WidgetTreeRequest):
    """Extracts the widget tree from all Widget classes in a .dart file."""
    abs_project = os.path.realpath(request.project_path)
    abs_file = os.path.realpath(os.path.join(abs_project, request.file_path))

    if not abs_file.startswith(abs_project):
        raise HTTPException(status_code=400, detail="Path traversal detected.")
    if not os.path.isfile(abs_file):
        raise HTTPException(status_code=404, detail=f"File not found: {request.file_path}")

    try:
        extractor = WidgetTreeExtractor()
        widgets = extractor.extract(abs_file)
        return {
            "project_path": request.project_path,
            "file_path": request.file_path,
            "widgets": widgets,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction error: {str(e)}")
