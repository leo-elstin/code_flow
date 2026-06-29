import os
import ctypes
from tree_sitter import Language, Parser, Query, QueryCursor

# Load the custom Dart library (following analyst.py pattern)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DART_LIB_PATH = os.path.join(BASE_DIR, "lib/dart.so")


def load_custom_language(path, name):
    """Loads a tree-sitter language from a shared library (.so file)."""
    lib = ctypes.cdll.LoadLibrary(path)
    lang_func = getattr(lib, f"tree_sitter_{name}")
    lang_func.restype = ctypes.c_void_p
    return Language(lang_func())


DART_LANGUAGE = load_custom_language(DART_LIB_PATH, "dart")
DART_PARSER = Parser(DART_LANGUAGE)

# ── Pre-compile all queries once at module load ────────────────────────────────
# Syntax mirrors dart.scm which is known to be valid for this grammar version.

_IMPORT_QUERY = Query(
    DART_LANGUAGE,
    "(library_import (import_specification (configurable_uri (uri (string_literal) @import.path))))"
)

_ENUM_QUERY = Query(
    DART_LANGUAGE,
    "(enum_declaration name: (identifier) @enum.name)"
)

# Matches every class.  The superclass capture is optional – if absent the
# whole pattern still matches so we won't miss plain classes.
_CLASS_QUERY = Query(
    DART_LANGUAGE,
    """
(class_declaration
  name: (identifier) @class.name) @class.definition
"""
)

_SUPER_QUERY = Query(
    DART_LANGUAGE,
    """
(class_declaration
  name: (identifier) @class.name
  (superclass (type_identifier) @class.super)) @class.definition
"""
)

# Match method and function signatures – identical to dart.scm
_METHOD_QUERY = Query(
    DART_LANGUAGE,
    """
(method_signature) @method.definition
(function_signature) @function.definition
"""
)


def _get_method_name(node) -> str | None:
    """Extract the method name from a method/function signature node.
    Walks children to find the first identifier, which is the name.
    """
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf8")
    return None


def _captures_to_list(captures):
    """Normalise captures to a list of (node, tag) regardless of dict/list format."""
    if isinstance(captures, list):
        return captures
    # dict format: {tag: [node, ...], ...}
    result = []
    for tag, nodes in captures.items():
        for node in nodes:
            result.append((node, tag))
    return result


class DartStructuralParser:
    @staticmethod
    def parse_file(file_path: str):
        """Parses a Dart file and extracts its structural skeleton."""
        if not os.path.exists(file_path):
            return None

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        tree = DART_PARSER.parse(bytes(content, "utf8"))
        root = tree.root_node

        structure = {
            "imports": [],
            "enums": [],
            "classes": [],
        }

        # ── Imports ────────────────────────────────────────────────────────────
        cursor = QueryCursor(_IMPORT_QUERY)
        for node, tag in _captures_to_list(cursor.captures(root)):
            if tag == "import.path":
                structure["imports"].append(
                    node.text.decode("utf8").strip("'\"")
                )

        # ── Enums ──────────────────────────────────────────────────────────────
        cursor = QueryCursor(_ENUM_QUERY)
        for node, tag in _captures_to_list(cursor.captures(root)):
            if tag == "enum.name":
                structure["enums"].append(node.text.decode("utf8"))

        # ── Classes (names only, no superclass) ────────────────────────────────
        cursor = QueryCursor(_CLASS_QUERY)
        class_items = _captures_to_list(cursor.captures(root))

        # Build map keyed by byte-range so we can attach extra data later
        classes_map: dict = {}
        for node, tag in class_items:
            if tag != "class.definition":
                continue
            node_id = (node.start_byte, node.end_byte)
            if node_id not in classes_map:
                classes_map[node_id] = {
                    "name": "",
                    "extends": None,
                    "methods": [],
                    "_node": node,
                }

        # Fill names
        for node, tag in class_items:
            if tag != "class.name":
                continue
            parent = node.parent
            while parent and parent.type != "class_declaration":
                parent = parent.parent
            if not parent:
                continue
            node_id = (parent.start_byte, parent.end_byte)
            if node_id in classes_map:
                classes_map[node_id]["name"] = node.text.decode("utf8")

        # ── Superclasses ───────────────────────────────────────────────────────
        cursor = QueryCursor(_SUPER_QUERY)
        for node, tag in _captures_to_list(cursor.captures(root)):
            if tag != "class.super":
                continue
            parent = node.parent  # type_identifier -> superclass -> class_declaration
            while parent and parent.type != "class_declaration":
                parent = parent.parent
            if not parent:
                continue
            node_id = (parent.start_byte, parent.end_byte)
            if node_id in classes_map:
                classes_map[node_id]["extends"] = node.text.decode("utf8")

        # ── Methods per class ──────────────────────────────────────────────────
        for info in classes_map.values():
            class_node = info.pop("_node")
            cursor = QueryCursor(_METHOD_QUERY)
            seen: set = set()
            for m_node, m_tag in _captures_to_list(cursor.captures(class_node)):
                method_name = _get_method_name(m_node)
                if method_name and method_name not in seen:
                    info["methods"].append(method_name)
                    seen.add(method_name)
            structure["classes"].append(info)

        return structure
