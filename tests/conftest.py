import sys
import importlib.util
from pathlib import Path
import pytest

# Helper to import the hyphenated module
def import_file_commander():
    file_path = Path(__file__).parent.parent / "file-commander.py"
    spec = importlib.util.spec_from_file_location("file_commander", file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["file_commander"] = module
    spec.loader.exec_module(module)
    return module

fc = import_file_commander()

@pytest.fixture
def mock_file_structure(tmp_path):
    """Create a temporary file structure for testing."""
    root = tmp_path / "test_root"
    root.mkdir()
    
    # Create some files
    (root / "doc1.txt").write_text("content")
    (root / "image.jpg").write_text("content")
    (root / "folder1").mkdir()
    (root / "folder1" / "doc2.pdf").write_text("content")
    
    return root

@pytest.fixture
def path_utils():
    return fc.PathUtils

@pytest.fixture
def trie():
    return fc.Trie()

@pytest.fixture
def search_index():
    return fc.FileSearchIndex()
