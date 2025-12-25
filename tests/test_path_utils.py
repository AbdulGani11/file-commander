from pathlib import Path
import pytest

def test_is_safe_filename(path_utils):
    # Valid names
    assert path_utils.is_safe_filename("document.pdf")
    assert path_utils.is_safe_filename("New Folder")
    
    # Invalid names (empty)
    assert not path_utils.is_safe_filename("")
    assert not path_utils.is_safe_filename("   ")
    
    # Directory traversal attempts
    assert not path_utils.is_safe_filename("../escaped")
    assert not path_utils.is_safe_filename("..\\escaped")
    
    # Windows invalid characters
    assert not path_utils.is_safe_filename("invalid<")
    assert not path_utils.is_safe_filename("invalid>")
    assert not path_utils.is_safe_filename("invalid:")
    assert not path_utils.is_safe_filename("invalid\"")
    assert not path_utils.is_safe_filename("invalid|")
    assert not path_utils.is_safe_filename("invalid?")
    assert not path_utils.is_safe_filename("invalid*")

def test_get_drive_path(path_utils):
    assert path_utils.get_drive_path("c") == Path("C:/")
    assert path_utils.get_drive_path("D") == Path("D:/")

def test_get_item_type(path_utils, tmp_path):
    # Create test items
    d = tmp_path / "folder"
    d.mkdir()
    f = tmp_path / "file.txt"
    f.touch()
    
    assert path_utils.get_item_type(d) == "folder"
    assert path_utils.get_item_type(f) == "file"
