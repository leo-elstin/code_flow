import os
from app.agents.dart_parser import DartStructuralParser

class ProjectExplorer:
    def __init__(self, project_path: str):
        self.project_path = project_path
        self.prune_dirs = {'.venv', '__pycache__', 'node_modules', '.dart_tool', 'build', 'test', '.git', 'ios', 'android', 'macos', 'windows', 'linux'}

    def explore(self, sub_dir: str = "lib"):
        """Explores the project structure starting from sub_dir."""
        root_path = os.path.join(self.project_path, sub_dir)
        if not os.path.exists(root_path):
            return {"error": f"Path not found: {root_path}"}

        return self._walk(root_path)

    def _walk(self, current_path: str):
        """Recursively walks the directory and builds the tree."""
        name = os.path.basename(current_path)
        
        if os.path.isdir(current_path):
            children = []
            # List and sort for consistency
            try:
                entries = sorted(os.listdir(current_path))
            except PermissionError:
                return None

            for entry in entries:
                if entry.startswith('.') or entry in self.prune_dirs:
                    continue
                    
                full_path = os.path.join(current_path, entry)
                child_node = self._walk(full_path)
                if child_node:
                    children.append(child_node)
            
            return {
                "name": name,
                "type": "directory",
                "path": os.path.relpath(current_path, self.project_path),
                "children": children
            }
        else:
            # File node
            node = {
                "name": name,
                "type": "file",
                "path": os.path.relpath(current_path, self.project_path)
            }
            
            if name.endswith(".dart"):
                # Extract structure for Dart files
                try:
                    node["structure"] = DartStructuralParser.parse_file(current_path)
                except Exception as e:
                    node["structure_error"] = str(e)
            
            return node
