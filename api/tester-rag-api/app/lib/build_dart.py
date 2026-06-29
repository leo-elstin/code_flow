import os
import subprocess
from tree_sitter import Language


def build_dart_grammar():
    # 1. Get the directory of the current script
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 2. Clone the grammar if not present (using the nielsenko repo)
    grammar_dir = os.path.join(current_dir, "tree-sitter-dart")
    if not os.path.exists(grammar_dir):
        print(f"Cloning tree-sitter-dart into {grammar_dir}...")
        subprocess.run(["git", "clone", "https://github.com/nielsenko/tree-sitter-dart.git", grammar_dir])

    # 3. Build the shared library manually using cc (since Language.build_library is deprecated)
    output_path = os.path.join(current_dir, "dart.so")
    src_dir = os.path.join(grammar_dir, "src")
    
    print(f"Compiling Dart grammar to {output_path}...")
    
    # Modern tree-sitter expects the shared library to be compiled from parser.c and scanner.c
    subprocess.run([
        "cc", "-O3", "-shared", "-fPIC",
        f"-I{src_dir}",
        os.path.join(src_dir, "parser.c"),
        os.path.join(src_dir, "scanner.c"),
        "-o", output_path
    ], check=True)
    
    print(f"Dart grammar compiled successfully to {output_path}")


if __name__ == "__main__":
    build_dart_grammar()