import os
from tree_sitter import Query, QueryCursor
from app.agents.dart_parser import DART_LANGUAGE, DART_PARSER, _captures_to_list

# Widget superclasses that indicate a screen/component
_WIDGET_SUPERS = (
    "StatelessWidget", "StatefulWidget",
    "ConsumerWidget", "ConsumerStatefulWidget",
    "State", "HookWidget", "HookConsumerWidget",
)

# Named arg slots that hold a single child widget
_CHILD_SLOTS = {
    "child", "body", "appBar", "drawer", "bottomNavigationBar",
    "leading", "trailing", "floatingActionButton", "title",
    "placeholder", "header", "footer", "sliver", "delegate",
}

# Named arg slots that hold a list of children
_CHILDREN_SLOTS = {"children", "actions", "tabs", "items", "slivers", "delegates"}

# Named arg slots captured as text props
_TEXT_PROPS = {"text", "data", "label", "hint", "hintText", "labelText", "semanticsLabel"}

_WIDGET_CLASS_QUERY = Query(
    DART_LANGUAGE,
    """
(class_declaration
  name: (identifier) @class.name) @class.definition
""",
)

_SUPER_QUERY = Query(
    DART_LANGUAGE,
    """
(class_declaration
  name: (identifier) @class.name
  (superclass (type_identifier) @class.super)) @class.definition
""",
)


class WidgetTreeExtractor:
    """Extracts a visual widget tree from a Dart file's build() methods."""

    def extract(self, file_path: str) -> list[dict]:
        if not os.path.exists(file_path):
            return []

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        tree = DART_PARSER.parse(bytes(content, "utf-8"))
        root = tree.root_node

        # ── Pass 1: collect ALL classes with their superclass text ──────────
        all_classes = self._find_all_classes(root)

        # ── Pass 2: build lookup maps ───────────────────────────────────────
        # name → class_info (for all classes)
        by_name: dict[str, dict] = {c["name"]: c for c in all_classes if c["name"]}

        # Widget classes: the ones whose base is a known widget superclass
        # (exclude raw State subclasses — they're linked via StatefulWidget)
        _STANDALONE_SUPERS = {
            "StatelessWidget", "StatefulWidget",
            "ConsumerWidget", "ConsumerStatefulWidget",
            "HookWidget", "HookConsumerWidget",
        }

        results = []
        for info in all_classes:
            extends = info["extends"]
            if extends not in _STANDALONE_SUPERS:
                continue

            # If it's a stateful variant, we look for the linked _XState class
            is_stateful = any(s in extends for s in ("StatefulWidget", "HookWidget", "HookConsumerWidget"))
            
            if is_stateful:
                # Find the matching State class by naming convention
                state_name = f"_{info['name']}State"
                state_info = by_name.get(state_name)
                build_node = state_info["node"] if state_info else info["node"]
            else:
                build_node = info["node"]

            build_block = self._find_build_block(build_node)
            widget_tree = self._find_widget_in_block(build_block) if build_block else None

            results.append({
                "class_name": info["name"],
                "extends": extends,
                "tree": widget_tree,
            })

        return results

    # ------------------------------------------------------------------
    # Class discovery (text-based superclass, handles generics)
    # ------------------------------------------------------------------

    def _find_all_classes(self, root) -> list[dict]:
        """Return every class in the file with name, extends, and node."""
        cursor = QueryCursor(_WIDGET_CLASS_QUERY)
        captures = _captures_to_list(cursor.captures(root))

        classes: dict[tuple, dict] = {}
        for node, tag in captures:
            if tag == "class.definition":
                key = (node.start_byte, node.end_byte)
                if key not in classes:
                    # Extract superclass from raw source text
                    extends = self._extract_super_text(node)
                    classes[key] = {"name": "", "extends": extends, "node": node}
            elif tag == "class.name":
                parent = self._ancestor_of_type(node, "class_declaration")
                if parent:
                    key = (parent.start_byte, parent.end_byte)
                    if key in classes:
                        classes[key]["name"] = node.text.decode("utf-8")

        return list(classes.values())

    def _extract_super_text(self, class_node) -> str:
        """
        Extract the superclass name from a class_declaration node.
        Works for both plain ('extends Foo') and generic ('extends Foo<Bar>').
        Returns the base class name only (e.g. 'State' from 'State<InvoicePage>').
        """
        for child in class_node.children:
            if child.type == "superclass":
                raw = child.text.decode("utf-8").strip()
                # raw might be: "extends State<InvoicePage>" or "extends StatelessWidget"
                # Remove 'extends' keyword
                name = raw.replace("extends", "").strip()
                # Take only the part before '<' for generic types
                return name.split("<")[0].strip()
        return ""

    def _ancestor_of_type(self, node, type_name: str):
        parent = node.parent
        while parent and parent.type != type_name:
            parent = parent.parent
        return parent

    # ------------------------------------------------------------------
    # build() body discovery
    # ------------------------------------------------------------------

    def _find_build_block(self, class_node):
        """Find the build() method body within a class declaration."""
        # Find class_body first
        class_body = next((c for c in class_node.children if c.type == "class_body"), None)
        if not class_body:
            return None

        for member in class_body.children:
            if member.type != "class_member":
                continue
            
            # Check if this member is the build method
            if self._is_build_method(member):
                # Find the function_body sibling/child
                body = next((c for c in member.children if c.type == "function_body"), None)
                if body:
                    # Return the block inside the function body
                    block = next((c for c in body.children if c.type == "block"), None)
                    return block if block else body
        return None

    def _is_build_method(self, member_node) -> bool:
        """Check if a class_member node is the build() method."""
        for child in member_node.children:
            if child.type == "method_signature":
                # Look for 'build' identifier inside function_signature
                sig = next((c for c in child.children if c.type == "function_signature"), None)
                if sig:
                    ident = next((c for c in sig.children if c.type == "identifier"), None)
                    if ident and ident.text == b"build":
                        return True
        return False

    # ------------------------------------------------------------------
    # Widget tree parsing
    # ------------------------------------------------------------------

    def _find_widget_in_block(self, block_node) -> dict | None:
        """Find the returned widget expression in a block."""
        # Direct return statements
        for child in block_node.children:
            if child.type == "return_statement":
                # Return statement children: 'return', expression, ';'
                for expr in child.children:
                    if expr.type not in ("return", ";") and expr.text != b"return":
                        return self._parse_widget(expr)
        
        # Deep search (widget inside an if/try/etc.)
        return self._deep_find_return(block_node)

    def _deep_find_return(self, node, depth: int = 0) -> dict | None:
        if depth > 15:
            return None
        if node.type == "return_statement":
            for child in node.children:
                if child.type not in ("return", ";") and child.text != b"return":
                    return self._parse_widget(child)
        for child in node.children:
            r = self._deep_find_return(child, depth + 1)
            if r:
                return r
        return None

    def _parse_widget(self, node) -> dict:
        """Parse a widget constructor call node into a dict."""
        name = self._find_constructor_name(node)
        
        # In Dart tree-sitter, the arguments might be in a sibling 'selector' 
        # if 'node' is just the identifier.
        args = self._find_args_node(node)
        
        # If no args found in the node or its children, check siblings if it's an identifier
        if not args and node.type == "identifier":
            parent = node.parent
            if parent:
                # Find the selector that follows this identifier
                found_self = False
                for sibling in parent.children:
                    # Compare nodes by position since instances might differ
                    if not found_self:
                        if sibling.start_byte == node.start_byte and sibling.end_byte == node.end_byte:
                            found_self = True
                        continue
                    
                    if found_self:
                        if sibling.type == "selector":
                            args = self._find_args_node(sibling)
                            if args:
                                break
                        # If we hit another identifier or something else, stop
                        if sibling.type in ("identifier", "return_statement", "class_member"):
                            break

        widget: dict = {
            "type": name or "_Unknown",
            "props": {},
            "slots": {},
            "children": [],
        }
        
        if not name:
            raw = (node.text or b"").decode("utf-8", errors="replace")[:40]
            widget["props"]["raw"] = raw

        if args:
            self._parse_args(args, widget)
        return widget

    def _find_constructor_name(self, node) -> str | None:
        """Find the widget type name (e.g., 'Scaffold' or '_MyWidget')."""
        def is_widget_name(t: str) -> bool:
            if not t: return False
            if t[0].isupper(): return True
            if t.startswith("_") and len(t) > 1 and t[1].isupper(): return True
            return False

        if node.type == "identifier":
            t = node.text.decode("utf-8")
            if is_widget_name(t):
                return t
        
        # If it's a constructor call, the first child is usually the identifier
        if node.children:
            first = node.children[0]
            if first.type == "identifier":
                t = first.text.decode("utf-8")
                if is_widget_name(t):
                    return t
                    
        # Fallback recursive search for first widget-like identifier
        for child in node.children:
            r = self._find_constructor_name(child)
            if r:
                return r
        return None

    def _find_args_node(self, node):
        """Find the arguments node (the one containing individual arguments)."""
        if node.type == "arguments":
            return node
            
        for child in node.children:
            if child.type == "selector":
                # Deep search in selector
                res = self._find_args_node(child)
                if res: return res
            if child.type == "argument_part":
                res = self._find_args_node(child)
                if res: return res
            if child.type == "arguments":
                return child
        
        # Recursive search fallback
        for child in node.children:
            if child.type not in ("block", "class_body"): # Optimization
                r = self._find_args_node(child)
                if r: return r
        return None

    def _parse_args(self, args_node, widget: dict):
        """Recursively find and parse arguments from an arguments-related node."""
        for child in args_node.children:
            if child.type in ("arguments", "argument_part", "argument_list"):
                self._parse_args(child, widget)
            elif child.type in ("named_argument", "argument"):
                self._parse_named_arg(child, widget)

    def _parse_named_arg(self, arg_node, widget: dict):
        label = None
        value = None
        
        # In a named_argument, children are usually: label (with identifier), ':', expression
        for child in arg_node.children:
            if child.type == "label":
                ident = next((c for c in child.children if c.type == "identifier"), None)
                if ident:
                    label = ident.text.decode("utf-8").strip()
            elif child.type == "identifier" and label is None:
                # Fallback for older tree-sitter or different structure
                label = child.text.decode("utf-8").rstrip(":").strip()
            elif child.type not in (":", ",") and label is not None and value is None:
                value = child
        
        if not label or value is None:
            return

        if label in _TEXT_PROPS:
            if value.type == "string_literal":
                widget["props"][label] = value.text.decode("utf-8").strip("'\"")
            else:
                widget["props"][label] = "[dynamic]"
        elif label in _CHILD_SLOTS:
            widget["slots"][label] = self._parse_widget(value)
        elif label in _CHILDREN_SLOTS:
            widget["children"] = self._parse_children_list(value)
        elif value.type == "string_literal":
            widget["props"][label] = value.text.decode("utf-8").strip("'\"")
        elif value.type in ("true", "false", "null", "decimal_integer_literal"):
            widget["props"][label] = value.text.decode("utf-8")

    def _parse_children_list(self, node) -> list[dict]:
        """Parse a list of widgets (e.g., in 'children: [...]')."""
        children = []
        
        # Find the list_literal if it's wrapped
        list_node = node
        if node.type != "list_literal":
            list_node = next((c for c in node.children if c.type == "list_literal"), node)

        for child in list_node.children:
            if child.type in ("[", "]", ","):
                continue
            if child.type == "spread_element":
                children.append({"type": "_Spread", "props": {}, "slots": {}, "children": []})
            elif child.type == "if_element":
                children.append({"type": "_Conditional", "props": {}, "slots": {}, "children": []})
            else:
                w = self._parse_widget(child)
                if w:
                    children.append(w)
        return children
