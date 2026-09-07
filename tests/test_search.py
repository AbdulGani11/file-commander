from pathlib import Path

def test_trie_insert_and_search(trie):
    p1 = Path("C:/docs/report.pdf")
    p2 = Path("C:/docs/report_final.pdf")
    p3 = Path("C:/images/photo.jpg")
    
    # Insert files
    trie.insert("report", p1)
    trie.insert("report", p2)
    trie.insert("photo", p3)
    
    # Search prefixes
    assert p1 in trie.search_prefix("rep")
    assert p2 in trie.search_prefix("rep")
    assert p3 not in trie.search_prefix("rep")
    
    # Exact prefix
    results = trie.search_prefix("photo")
    assert len(results) == 1
    assert results[0] == p3
    
    # Non-existent
    assert len(trie.search_prefix("xyz")) == 0

def test_trie_case_insensitivity(trie):
    p = Path("test.txt")
    trie.insert("Test", p)

    # Trie.insert lowercases every character on traversal,
    # so searches are case-insensitive regardless of how the word was inserted.
    assert p in trie.search_prefix("test")
    assert p in trie.search_prefix("TEST")

def test_search_index_add_and_search(search_index, tmp_path):
    # Setup files
    f1 = tmp_path / "important_document.txt"
    f1.touch()
    f2 = tmp_path / "my_image.jpg"
    f2.touch()
    
    # Add to index
    search_index.add_file(f1)
    search_index.add_file(f2)
    
    # Search
    results = search_index.search("important")
    assert any(str(f1) == str(r) for r in results)
    
    results = search_index.search("image")
    assert any(str(f2) == str(r) for r in results)

def test_search_index_relevance(search_index, tmp_path):
    # "test.txt" is shorter/better match than "test_long_file_name.txt"
    short_match = tmp_path / "test.txt"
    long_match = tmp_path / "test_long_file_name.txt"
    
    short_match.touch()
    long_match.touch()
    
    search_index.add_file(short_match)
    search_index.add_file(long_match)
    
    results = search_index.search("test")
    
    # Ensure both are found
    paths = [str(p) for p in results]
    assert str(short_match) in paths
    assert str(long_match) in paths
    
    # The exact sorting depends on the scoring algorithm, but short exact matches usually rank higher
    # Given the implementation, let's just verify they are found for now.


def test_old_cache_is_rejected(search_index, tmp_path):
    """A cache written under different indexing rules must be discarded.

    load_index repopulates straight from SQLite without re-applying
    SKIP_DIRECTORIES, so a stale cache would otherwise resurrect every path the
    current exclusions are meant to remove.
    """
    import sqlite3
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "FileFind", Path(__file__).parent.parent / "FileFind.py"
    )
    ff = importlib.util.module_from_spec(spec)
    sys.modules["FileFind"] = ff
    spec.loader.exec_module(ff)

    db = tmp_path / "stale.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE schema_version (version INTEGER)")
    conn.execute(
        "CREATE TABLE files (path TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "is_dir INTEGER NOT NULL)"
    )
    # A version older than the current one
    conn.execute("INSERT INTO schema_version VALUES (?)", (ff.CACHE_SCHEMA_VERSION - 1,))
    conn.execute(
        "INSERT INTO files VALUES (?, ?, ?)",
        (r"C:\proj\venv\Lib\site-packages\pip\__init__.py", "__init__.py", 0),
    )
    conn.commit()
    conn.close()

    assert search_index.load_index(db) is False
    assert search_index.total_items == 0


# Persistence round-trip (previously untested)


def _load_ff():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location(
        "FileFind", Path(__file__).parent.parent / "FileFind.py"
    )
    ff = importlib.util.module_from_spec(spec)
    sys.modules["FileFind"] = ff
    spec.loader.exec_module(ff)
    return ff


def test_cache_round_trip_preserves_index(tmp_path):
    ff = _load_ff()
    src = tmp_path / "files"
    src.mkdir()
    for n in ["quarterly_business_review.docx", "notes.txt"]:
        (src / n).touch()

    db = tmp_path / "cache.db"
    saved = ff.FileSearchIndex()
    # add_file, not index_folder: see test_tmp_path_is_excluded_from_crawling
    for child in src.iterdir():
        saved.add_file(child)
    assert saved.save_index(db) is True

    loaded = ff.FileSearchIndex()
    assert loaded.load_index(db) is True
    assert loaded.total_items == saved.total_items
    # Acronym tokens must be rebuilt on load, not only on first index
    assert "qbr" in loaded.word_index
    assert len(loaded.search("qbr", 10)) == len(saved.search("qbr", 10))


def test_loading_same_cache_twice_does_not_duplicate(tmp_path):
    ff = _load_ff()
    src = tmp_path / "files"
    src.mkdir()
    (src / "a.txt").touch()

    db = tmp_path / "cache.db"
    first = ff.FileSearchIndex()
    first.add_file(src / "a.txt")
    first.save_index(db)

    twice = ff.FileSearchIndex()
    twice.load_index(db)
    twice.load_index(db)
    assert twice.total_items == first.total_items


def test_corrupt_cache_is_rejected_not_raised(tmp_path):
    """A damaged cache must trigger a rebuild, not crash startup.

    Regression guard: load_index caught only sqlite3.OperationalError, while a
    corrupt file raises sqlite3.DatabaseError, so a damaged cache took the
    application down on launch.
    """
    ff = _load_ff()
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"definitely not a sqlite database")

    index = ff.FileSearchIndex()
    assert index.load_index(bad) is False      # must not raise
    assert index.total_items == 0


def test_unwritable_cache_does_not_lose_the_index(tmp_path):
    """A failed cache write must not discard a completed index build."""
    ff = _load_ff()
    index = ff.FileSearchIndex()
    (tmp_path / "a.txt").touch()
    index.add_file(tmp_path / "a.txt")

    # A directory is never a valid SQLite file
    assert index.save_index(tmp_path) is False
    assert index.total_items == 1              # index survives the failure


def test_acronym_edge_cases():
    ff = _load_ff()
    acronym = ff.FileSearchIndex._acronym

    assert acronym("quarterly_business_review.docx") == "qbr"
    assert acronym("my-file-name.txt") == "mfn"
    assert acronym("12_34_56.log") == "135"
    assert acronym("notepad.exe") is None      # single word has no acronym
    assert acronym(".gitignore") is None       # no tokens at all


def test_tmp_path_is_excluded_from_crawling(tmp_path):
    """pytest's tmp_path sits under AppData, which SKIP_DIRECTORIES excludes.

    index_folder therefore indexes nothing under tmp_path and fails silently,
    which makes tests look like they pass while asserting on an empty index.
    Tests must call add_file directly. Documented here so the next person does
    not lose an hour to it.
    """
    ff = _load_ff()
    assert ff.PathUtils.should_skip_directory(tmp_path)

    (tmp_path / "a.txt").touch()
    index = ff.FileSearchIndex()

    assert index.index_folder(tmp_path) >= 0
    assert index.total_items == 0, "index_folder unexpectedly worked under tmp_path"

    index.add_file(tmp_path / "a.txt")
    assert index.total_items == 1


def _allow_tmp_path(ff):
    """Let SKIP_DIRECTORIES tolerate tmp_path for the duration of a test.

    pytest's tmp_path lives under AppData, which the writer loop's insert path
    filters out. Dropping that one entry lets these tests exercise the loop
    rather than the exclusion policy. Returns the original set to restore.
    """
    original = set(ff.SKIP_DIRECTORIES)
    ff.SKIP_DIRECTORIES.discard("appdata")
    return original


# Background writer loop (previously untested)


def test_writer_loop_applies_insert_and_delete(tmp_path):
    """Live filesystem events must reach the in-memory index."""
    import queue
    import threading
    import time

    ff = _load_ff()
    original = _allow_tmp_path(ff)
    index = ff.FileSearchIndex()
    events = queue.Queue()
    stop = threading.Event()

    worker = threading.Thread(
        target=ff.FileCommander._writer_loop,
        args=(index, tmp_path / "w.db", events, stop),
        daemon=True,
    )
    worker.start()
    try:
        target = tmp_path / "brand_new_document.txt"
        target.touch()
        events.put(("insert", target, None))

        deadline = time.time() + 5
        while time.time() < deadline and not index.search("brand", 5):
            time.sleep(0.02)
        assert target in index.search("brand", 5)
        assert "bnd" in index.word_index          # acronym added live too

        events.put(("delete", target, None))
        deadline = time.time() + 5
        while time.time() < deadline and index.search("brand", 5):
            time.sleep(0.02)
        assert target not in index.search("brand", 5)
    finally:
        stop.set()
        worker.join(timeout=3)
        ff.SKIP_DIRECTORIES.clear()
        ff.SKIP_DIRECTORIES.update(original)

    assert not worker.is_alive()


def test_writer_loop_survives_a_bad_event(tmp_path):
    """One malformed event must not kill live synchronization for the session."""
    import queue
    import threading
    import time

    ff = _load_ff()
    original = _allow_tmp_path(ff)
    index = ff.FileSearchIndex()
    events = queue.Queue()
    stop = threading.Event()

    worker = threading.Thread(
        target=ff.FileCommander._writer_loop,
        args=(index, tmp_path / "w.db", events, stop),
        daemon=True,
    )
    worker.start()
    try:
        events.put(("insert", None, None))       # None path raises inside the loop
        events.put(("nonsense_op", tmp_path, None))
        time.sleep(0.3)
        assert worker.is_alive()

        survivor = tmp_path / "after_poison.txt"
        survivor.touch()
        events.put(("insert", survivor, None))
        deadline = time.time() + 5
        while time.time() < deadline and not index.search("after", 5):
            time.sleep(0.02)
        assert survivor in index.search("after", 5)
    finally:
        stop.set()
        worker.join(timeout=3)
        ff.SKIP_DIRECTORIES.clear()
        ff.SKIP_DIRECTORIES.update(original)


def test_writer_loop_exits_when_database_is_unusable(tmp_path):
    """An unopenable database must end the thread cleanly, not raise.

    Regression guard: sqlite3.connect sat outside the try block, so a bad path
    killed the writer thread with an exception instead of returning.
    """
    import queue
    import threading
    import time

    ff = _load_ff()
    stop = threading.Event()
    worker = threading.Thread(
        target=ff.FileCommander._writer_loop,
        # a directory is never a valid SQLite file
        args=(ff.FileSearchIndex(), tmp_path, queue.Queue(), stop),
        daemon=True,
    )
    worker.start()
    time.sleep(0.3)
    assert not worker.is_alive()
    stop.set()
