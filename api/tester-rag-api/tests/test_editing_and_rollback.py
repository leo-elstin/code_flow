import os
import subprocess
import pytest
from app.tools.edit_file import edit_file, EditFileError
from app.tools.filesystem import roll_back_file

def test_surgical_edit_success(tmp_path):
    file_path = tmp_path / "lib_test.dart"
    content = "class A {\n  void foo() {\n    print('hello');\n  }\n}"
    file_path.write_text(content, encoding="utf-8")
    
    edit_file(
        str(tmp_path),
        "lib_test.dart",
        "print('hello');",
        "print('world');"
    )
    
    new_content = file_path.read_text(encoding="utf-8")
    assert "print('world');" in new_content
    assert "print('hello');" not in new_content

def test_surgical_edit_missing_file(tmp_path):
    with pytest.raises(EditFileError, match="Target file does not exist"):
        edit_file(str(tmp_path), "nonexistent.dart", "a", "b")

def test_surgical_edit_not_found(tmp_path):
    file_path = tmp_path / "lib_test.dart"
    file_path.write_text("class A {}", encoding="utf-8")
    
    with pytest.raises(EditFileError, match="Target content not found"):
        edit_file(str(tmp_path), "lib_test.dart", "print('hello');", "print('world');")

def test_surgical_edit_ambiguous(tmp_path):
    file_path = tmp_path / "lib_test.dart"
    content = "print('hello');\nprint('hello');"
    file_path.write_text(content, encoding="utf-8")
    
    with pytest.raises(EditFileError, match="found 2 times"):
        edit_file(str(tmp_path), "lib_test.dart", "print('hello');", "print('world');")

def test_rollback_file(tmp_path):
    # Initialize a temporary git repository
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, capture_output=True)
    
    test_file = tmp_path / "lib_test.dart"
    original = "class A {}"
    test_file.write_text(original, encoding="utf-8")
    
    subprocess.run(["git", "add", "lib_test.dart"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True)
    
    # Modify the file
    test_file.write_text("class B {}", encoding="utf-8")
    assert test_file.read_text(encoding="utf-8") == "class B {}"
    
    # Revert it
    roll_back_file(str(tmp_path), "lib_test.dart")
    assert test_file.read_text(encoding="utf-8") == original
