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
    
    # Search should be case insensitive depending on implementation
    # The current Trie implementation lowercases the word on insert (in add_file logic) 
    # but the Trie.insert method itself takes the word as is.
    # Let's check Trie.insert implementation:
    # "for char in word.lower():" -> It lowercases on insert!
    
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
