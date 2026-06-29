import os
from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.analyst import CodeAnalyst
from app.services.embedding import get_embeddings
from app.services.vector_store import search_qdrant

router = APIRouter()
analyst = CodeAnalyst()

class ContextRequest(BaseModel):
    query: str
    project_path: str

def should_skip_file(file_path: str) -> bool:
    """Filter out noise files that bloat context without providing logic."""
    noise_patterns = [
        ".g.dart", ".freezed.dart", "l10n", "generated", 
        "colors.dart", "styles.dart", "assets.dart", "theme.dart",
        ".config.dart", "firebase_options.dart", "dart-junitreport", "tool/",
        "_test.dart", "/test/"
    ]
    return any(pattern in file_path.lower() for pattern in noise_patterns)

def resolve_import_path(current_file: str, import_str: str, project_path: str) -> str | None:
    """Basic resolution for relative and package imports."""
    if should_skip_file(import_str):
        return None
        
    if import_str.startswith("package:"):
        # Assuming package:app_name/path/to/file.dart -> project_path/lib/path/to/file.dart
        parts = import_str.split("/", 1)
        if len(parts) > 1:
            # We look for 'lib' folder which is standard in Flutter
            potential_path = os.path.join(project_path, "lib", parts[1])
            if os.path.exists(potential_path):
                return potential_path
    
    # Handle relative imports
    if import_str.endswith(".dart") or import_str.endswith(".py"):
        base_dir = os.path.dirname(current_file)
        potential_path = os.path.abspath(os.path.join(base_dir, import_str))
        if os.path.exists(potential_path):
            return potential_path
            
    return None

@router.post("/context")
async def get_module_context(request: ContextRequest):
    """
    Retrieves full file contents for all files related to a specific module.
    Uses Hybrid Search + Tree-sitter Dependency Expansion.
    """
    # 1. Improved Keyword Extraction
    stop_words = {"module", "write", "the", "test", "cases", "for", "code", "related"}
    keywords = [word.lower() for word in request.query.split() if word.lower() not in stop_words and len(word) > 2]
    print(f"Keywords extracted: {keywords}")
    
    initial_files = set()

    # 2. Semantic Search with Score Threshold
    vectors = await get_embeddings([request.query], task_type="CODE_RETRIEVAL_QUERY")
    if vectors:
        semantic_results = search_qdrant("flutter_codebase", vectors[0], limit=30)
        for res in semantic_results:
            # Lowering threshold slightly for better coverage
            if res["score"] > 0.5:
                file_path = res["metadata"].get("file_path")
                if file_path:
                    print(f"Semantic match found: {file_path} (Score: {res['score']})")
                    initial_files.add(file_path)

    # 3. Path Search (Keyword) - More targeted
    print(f"Walking project path: {request.project_path}")
    if not os.path.exists(request.project_path):
        print(f"ERROR: Project path does not exist: {request.project_path}")
    
    for root, dirs, files in os.walk(request.project_path):
        # Prune large/irrelevant directories early
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in (
            '.venv', '__pycache__', 'node_modules', '.dart_tool', 'build', 'dart-junitreport', 'test'
        )]
        
        path_contains_keyword = any(kw in root.lower() for kw in keywords)
        
        for file in files:
            if not file.endswith((".dart", ".py")):
                continue
                
            file_path = os.path.join(root, file)
            
            # Skip noise files immediately
            if should_skip_file(file_path):
                continue
                
            if any(kw in file.lower() for kw in keywords) or path_contains_keyword:
                print(f"Keyword match found: {file_path}")
                initial_files.add(file_path)

    # 3. Recursive Dependency Expansion (Tree-sitter)
    final_files = set()
    to_process = list(initial_files)
    depth = 2 # Follow dependencies up to 2 levels deep
    
    while to_process and depth > 0:
        next_batch = []
        for file_path in to_process:
            if file_path in final_files: continue
            final_files.add(file_path)
            
            # Get imports using tree-sitter
            imports = analyst.get_imports(file_path)
            for imp in imports:
                resolved = resolve_import_path(file_path, imp, request.project_path)
                if resolved and resolved not in final_files:
                    next_batch.append(resolved)
        
        to_process = next_batch
        depth -= 1

    # 4. Collate file contents with size limits
    context_data = []
    total_chars = 0
    MAX_CHARS = 200000 # Increased for gpt-5-mini (500k TPM)
    
    def get_file_priority(path: str) -> int:
        """Determines the importance of a file for understanding 'flows'."""
        path_lower = path.lower()
        # High Priority: UI Pages and Business Logic
        if any(term in path_lower for term in ["_page.dart", "_screen.dart"]):
            return 1
        if any(term in path_lower for term in ["_cubit.dart", "_bloc.dart", "_service.dart"]):
            return 2
        # Medium Priority: Main implementation files
        if path in initial_files:
            return 0 # Initial matches are always highest
        # Low Priority: Data structures and boilerplate
        if any(term in path_lower for term in ["_dto.dart", "_model.dart", "_entity.dart", ".cg.dart"]):
            return 4
        return 3

    # Sort files by priority then by whether they were initial matches
    sorted_files = sorted(final_files, key=lambda x: (get_file_priority(x), x not in initial_files))
    
    for file_path in sorted_files:
        if total_chars > MAX_CHARS:
            break
            
        if should_skip_file(file_path):
            continue

        try:
            if os.path.exists(file_path) and os.path.isfile(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if len(content) > MAX_CHARS: # Individual file too large? Skip or truncate
                         content = content[:MAX_CHARS] + "\n... [TRUNCATED]"
                    
                    context_data.append({
                        "file_path": file_path,
                        "content": content
                    })
                    total_chars += len(content)
        except Exception:
            pass

    return {
        "module_query": request.query,
        "files_found": [f["file_path"] for f in context_data],
        "context": context_data,
        "total_chars": total_chars
    }
