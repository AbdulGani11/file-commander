import sys
import importlib.util
from pathlib import Path
import pytest

# Helper to import the module
def import_filefind():
    file_path = Path(__file__).parent.parent / "FileFind.py"
    spec = importlib.util.spec_from_file_location("FileFind", file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["FileFind"] = module
    spec.loader.exec_module(module)
    return module

fc = import_filefind()

@pytest.fixture
def path_utils():
    return fc.PathUtils

@pytest.fixture
def trie():
    return fc.Trie()

@pytest.fixture
def search_index():
    return fc.FileSearchIndex()


@pytest.fixture(autouse=True)
def isolate_launcher_settings(tmp_path, monkeypatch):
    """Keep the overlay's remembered position out of the real home directory.

    The overlay saves its position and scale on shutdown, and every Qt test
    closes an overlay. Without this, a run would write an offscreen test
    window's coordinates into ~/.filefind_launcher.json and move the user's
    real launcher the next time they started it.
    """
    try:
        from launcher.ui import overlay
    except ImportError:
        return              # PySide6 absent; those tests skip anyway
    monkeypatch.setattr(
        overlay, "SETTINGS_PATH", tmp_path / "launcher.json", raising=False
    )


@pytest.fixture(autouse=True)
def isolate_store_apps(tmp_path, monkeypatch):
    """Keep the Store app cache and its PowerShell call out of the test run.

    Registering an AppPlugin calls init(), which starts a background refresh
    that shells out to PowerShell and writes ~/.filefind_apps.json. Left alone
    that would make every test that touches the plugin slow, dependent on which
    applications happen to be installed, and a writer to the real home
    directory. Tests that want the refresh call _refresh_store_apps directly.
    """
    from launcher.handlers import apps

    monkeypatch.setattr(apps, "UWP_CACHE_PATH", tmp_path / "apps.json")
    monkeypatch.setattr(
        apps.AppPlugin, "refresh_store_apps_async", lambda self: None
    )