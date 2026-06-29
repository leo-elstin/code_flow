import os
import ctypes
from typing import Any

import tree_sitter_python
from tree_sitter import Language, Parser, Query, QueryCursor

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DART_LIB_PATH = os.path.join(BASE_DIR, "lib/dart.so")
QUERIES_DIR = os.path.join(BASE_DIR, "queries")


def load_custom_language(path, name):
    """Loads a tree-sitter language from a shared library (.so file)."""
    lib = ctypes.cdll.LoadLibrary(path)
    lang_func = getattr(lib, f"tree_sitter_{name}")
    lang_func.restype = ctypes.c_void_p
    return Language(lang_func())


class CodeAnalyst:
    def __init__(self):
        # Initialize Parsers
        self.parsers = {
            "dart": Parser(load_custom_language(DART_LIB_PATH, "dart")),
            "py": Parser(Language(tree_sitter_python.language()))
        }

        # Load Queries
        self.queries = {
            "dart": self._load_query("dart.scm"),
            "py": self._load_query("python.scm")
        }

    def _load_query(self, filename):
        path = os.path.join(QUERIES_DIR, filename)
        with open(path, "r") as f:
            return f.read()

    def chunk_file(self, file_path: str):
        ext = file_path.split(".")[-1]
        if ext not in self.parsers:
            return None

        with open(file_path, 'rb') as f:
            code = f.read()
            tree = self.parsers[ext].parse(code)

        query = Query(self.parsers[ext].language, self.queries[ext])
        cursor = QueryCursor(query)
        captures_dict = cursor.captures(tree.root_node)

        chunks = []
        for tag, nodes in captures_dict.items():
            for node in nodes:
                chunks.append({
                    "content": code[node.start_byte:node.end_byte].decode('utf8'),
                    "metadata": {
                        "file_path": file_path,
                        "language": ext,
                        "symbol_type": tag,
                        "start_line": node.start_point[0]
                    }
                })
        return chunks

    def get_imports(self, file_path: str):
        """Extracts import paths from a file using tree-sitter."""
        ext = file_path.split(".")[-1]
        if ext not in self.parsers:
            return []

        with open(file_path, 'rb') as f:
            code = f.read()
            tree = self.parsers[ext].parse(code)

        # Import-specific queries
        import_query_str = ""
        if ext == "dart":
            import_query_str = "(library_import (import_specification (configurable_uri (uri (string_literal) @import.path))))"
        elif ext == "py":
            import_query_str = """
            (import_statement (dotted_name) @import.path)
            (import_from_statement module_name: (dotted_name) @import.path)
            """

        query = Query(self.parsers[ext].language, import_query_str)
        cursor = QueryCursor(query)
        captures_dict = cursor.captures(tree.root_node)

        imports = []
        for nodes in captures_dict.values():
            for node in nodes:
                path = code[node.start_byte:node.end_byte].decode('utf8')
                # Clean up quotes for Dart/Python string literals
                path = path.strip("'").strip('"')
                imports.append(path)
        return list(set(imports))

    def get_symbols(self, file_path: str) -> list[dict[str, Any]]:
        """Extract class/function/method names from a source file."""
        ext = file_path.split(".")[-1]
        if ext not in self.parsers:
            return []

        if ext not in ("dart", "py"):
            return []

        with open(file_path, "rb") as handle:
            code = handle.read()
        tree = self.parsers[ext].parse(code)

        if ext == "dart":
            query_specs = [
                ("class", "(class_declaration name: (identifier) @name)"),
                ("function", "(function_signature name: (identifier) @name)"),
                ("method", "(method_declaration name: (identifier) @name)"),
            ]
        else:
            query_specs = [
                ("class", "(class_definition name: (identifier) @name)"),
                ("function", "(function_definition name: (identifier) @name)"),
            ]

        symbols: list[dict[str, Any]] = []
        seen: set[tuple[str, int, str]] = set()
        for kind, query_str in query_specs:
            try:
                query = Query(self.parsers[ext].language, query_str)
            except Exception:
                continue
            cursor = QueryCursor(query)
            captures_dict = cursor.captures(tree.root_node)
            for node in captures_dict.get("name", []):
                name = code[node.start_byte : node.end_byte].decode("utf-8")
                key = (name, node.start_point[0], kind)
                if key in seen:
                    continue
                seen.add(key)
                symbols.append(
                    {
                        "name": name,
                        "kind": kind,
                        "line": node.start_point[0] + 1,
                    }
                )
        return symbols
