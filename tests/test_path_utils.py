from pathlib import Path

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

    # Windows reserved names
    assert not path_utils.is_safe_filename("CON")
    assert not path_utils.is_safe_filename("con.txt")
    assert not path_utils.is_safe_filename("NUL")
    assert not path_utils.is_safe_filename("COM1")
    assert not path_utils.is_safe_filename("LPT1.log")

    # Names ending with period or space
    assert not path_utils.is_safe_filename("file.")
    assert not path_utils.is_safe_filename("file ")

    # Backslash in name (path traversal)
    assert not path_utils.is_safe_filename("sub\\file")

    # Forward slash in name (path traversal / silent directory move)
    assert not path_utils.is_safe_filename("sub/file")
    assert not path_utils.is_safe_filename("/absolute")  # leading slash escapes to root

    # Filename length limits (Windows max = 255 chars)
    assert path_utils.is_safe_filename("a" * 255)  # Exactly 255 chars - valid
    assert not path_utils.is_safe_filename("a" * 256)  # 256 chars - invalid

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


def test_developer_directories_are_skipped(path_utils):
    """Virtualenvs and tool caches must never reach the index.

    Regression guard: indexing this repository without these exclusions gave
    4,720 items, 4,650 of them from `venv` alone, burying the real files.
    """
    skipped = [
        r"C:\proj\venv\Lib\site-packages\pip\__init__.py",
        r"C:\proj\.venv\Lib\x.py",
        r"C:\proj\.claude\worktrees\a\Readme.md",
        r"C:\proj\.pytest_cache\v\cache\nodeids",
        r"C:\proj\node_modules\react\index.js",
        r"C:\proj\__pycache__\mod.cpython-311.pyc",
        r"C:\proj\.mypy_cache\3.11\x.json",
        r"C:\proj\.idea\workspace.xml",
    ]
    for raw in skipped:
        assert path_utils.should_skip_directory(Path(raw)), raw


def test_site_packages_catches_unconventional_env_names(path_utils):
    """An environment folder need not be called venv to be excluded."""
    assert path_utils.should_skip_directory(
        Path(r"C:\proj\my-weird-env\Lib\site-packages\thing.py")
    )


def test_excluded_directory_itself_is_skipped(path_utils):
    """The folder is skipped too, not only its contents.

    Checking only the parent left the excluded folder indexable, so searching
    for "venv" returned the directory whose contents had just been excluded.
    """
    assert path_utils.should_skip_directory(Path(r"C:\proj\venv"))
    assert path_utils.should_skip_directory(Path(r"C:\proj\__pycache__"))


def test_real_project_directories_are_not_skipped(path_utils):
    """The exclusions must not swallow ordinary user folders."""
    kept = [
        r"C:\proj\src\main.py",
        r"C:\Users\me\Documents\report.docx",
        r"D:\Photos\2026\holiday.jpg",
        r"C:\proj\tests\test_search.py",
        r"C:\proj\environment\notes.txt",   # "env" alone must not match
    ]
    for raw in kept:
        assert not path_utils.should_skip_directory(Path(raw)), raw
