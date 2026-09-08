# FileFind

FileFind is a fast local file search tool and application launcher for Windows. It replaces slow and unpredictable system search with fast in memory search trees, word indexes, a saved SQLite database cache, and a background watcher that tracks file changes instantly.

Search queries run with $O(m)$ prefix speed, where $m$ is the number of letters in your search word. This means searching takes the same small fraction of time whether you have ten files or a hundred thousand files on your computer.

## Main Features

- **Five Step Search System:** Looks for files using five methods in order: exact filename match, prefix tree lookup, word index search, substring scan, and optional fuzzy matching. The last two are expensive, so they run only when the cheaper ones return too little.
    
- **Fast Startup on Later Runs:** Saves index data to an SQLite database file at `~/.filefind_cache.db`. Measured on the development machine, a later start rebuilds the whole index of $39{,}930$ files from that cache in $0.71$ seconds, against $19.5$ seconds to crawl the same folders from disk.
    
- **Live Background Updates:** A background file listener monitors your folders and sends file creation, deletion, and rename events to a worker thread through a safe queue. The worker applies them in batches under a single database commit, which measured $74{,}835$ events per second: a burst of $15{,}000$ new files is absorbed in $0.2$ seconds rather than leaving results stale while it catches up.
    
- **Application Launcher:** Finds installed programs from four places Windows uses: Start Menu shortcuts, the registry `App Paths` key, your system `PATH`, and the Store app list. Store programs such as Calculator, Photos and Terminal have no executable on disk. Windows will only list them through PowerShell, which takes over a second, so the answer is cached in `~/.filefind_apps.json` and refreshed in the background rather than delaying every startup. Their icons have no file to come from either, and are fetched through the shell instead. Short name matching also lets you find tools quickly, such as typing `vsc` to find Visual Studio Code.
    
- **Floating Search Window:** A quick launcher mode keeps the index ready in memory. Pressing `Ctrl+Shift+F` opens a clean, borderless search window right on top of your screen, letting you find files without leaving your active work. Drag it anywhere, scale the whole interface to suit your display, and it reopens where you left it.
    
- **Modular Search Connectors:** The search system uses simple plug in connectors. Files, installed apps, and future search sources combine into one sorted results list. If you type new letters, earlier background searches cancel immediately so your computer stays fast.
    
- **Learns What You Open Most:** Tracks how many times you open each file inside `~/.filefind_history.json`. It gives a bonus of up to $+40$ points to your favorite files so they show up at the top of your search results.
    
- **Smart Folder Scanning:** Indexes important user folders on your main C: drive while scanning secondary drives and USB storage completely.
    
- **Built in Safety and Security:** Refuses to follow folder shortcut loops, prunes system folders from the scan, and hands files to Windows directly rather than building a shell command. It indexes and opens; it never writes.
    

## How Search Works

When you enter a search term, FileFind gathers matches through five steps:

|   |   |   |   |
|---|---|---|---|
|**Strategy**|**Search Speed**|**What It Does**|**When It Runs**|
|**Exact Match**|$O(1)$|Direct dictionary lookup|Checks if your search word matches the exact filename|
|**Prefix Tree**|$O(m)$ ($m = \text{word length}$)|Walks down the character tree|Runs on every search query|
|**Word Index**|$O(1)$ per word|Looks up individual words|Splits search words on dots, underscores, spaces, and dashes|
|**Substring Scan**|$O(n)$ ($n = \text{unique names}$)|Looks for letters inside names|Runs only when steps 1 to 3 return fewer results than needed|
|**Typo Matching**|$O(n)$ with fast C++ helper|Finds words with similar spelling|Runs when `rapidfuzz` is installed and other steps find fewer than $5$ files|

Each step adds matching files into a shared list. Steps 1 to 3 run on every search. Steps 4 and 5 do heavier work, so they run only when earlier steps return very few results.

## How Results Are Ranked

Results are sorted using a combined score formula:

$$\text{Total Score} = \text{Score}_{\text{structural}} + \text{Score}_{\text{length}} + \text{Score}_{\text{directory}} + \text{Score}_{\text{frequency}}$$

Scoring points include:

- **Exact Filename Match:** $+100$ points
    
- **Starts With Match:** $+80$ points
    
- **Contains Word Match:** $+50$ points
    
- **Short Name Bonus:** Up to $+30$ points, giving higher priority to shorter names
    
- **User Folder Priority:** $+10$ points for files in Documents, Desktop, or Downloads
    
- **Open History Bonus:** $+5$ points for each time you opened the file, up to $+40$ points total
    

Old search tree references are filtered out before ranking by checking every match against the active list of indexed files.

## Installation

### System Requirements

- Windows 10 or Windows 11
    
- Python 3.12 or newer
    

### Setup Steps

1. **Clone the repository:**
    
    ```
    git clone https://github.com/AbdulGani11/FileFind.git
    cd FileFind
    ```
    
2. **Create and activate a virtual environment:**
    
    ```
    python -m venv venv
    venv\Scripts\activate
    ```
    
3. **Install dependencies:**
    
    ```
    pip install -r requirements.txt
    ```
    

## Packages and Requirements

The search engine itself needs nothing outside the Python standard library. The packages marked **Launcher** are what the window needs; the two optional ones improve the engine but are never required.

|   |   |   |   |
|---|---|---|---|
|**Package**|**Minimum Version**|**Requirement**|**What Is Lost If Missing**|
|`PySide6-Essentials`|`>= 6.11.2`|Launcher|Drawing the graphical search window|
|`keyboard`|`>= 0.13.5`|Launcher|Global hotkey to open the floating window|
|`watchdog`|`>= 6.0.0`|Optional|Live background file change tracking|
|`rapidfuzz`|`>= 3.14.6`|Optional|Fuzzy matching when the other strategies find too little|
|`pytest`|`>= 9.1.1`|Development|Running automated tests|
|`pytest-cov`|`>= 7.1.0`|Development|Creating test coverage reports|

The engine checks the optional packages on import and sets status flags (`WATCHDOG_AVAILABLE`, `RAPIDFUZZ_AVAILABLE`), then degrades quietly rather than failing if either is absent.

Without `keyboard` the window still opens at launch, it simply has no global shortcut.

## How to Use FileFind

Run FileFind as a background launcher with a global hotkey and a graphical pop up window:

```
python run_launcher.py                  # Normal use: cached index and live watcher
python run_launcher.py --rebuild        # Discard the cache and scan from disk again
python run_launcher.py --repo           # Scan this project folder only (fast for testing)
python run_launcher.py --theme Light    # Pick a color theme: Dark, Darker, or Light
```

Launcher mode indexes your applications and files, turns on the background file watcher, sets up the `Ctrl+Shift+F` shortcut, and waits in the background. Pressing the shortcut opens a borderless search box in the center of your screen.

Because the index is already loaded in computer memory, opening the search box is instant.

#### Window Controls

- **Type:** Results update live as you type. The window shows $5$ rows at a time and scrolls through up to $20$ ranked matches.
    
- **Up and Down arrows:** Move through the results list. The selection wraps around, so holding an arrow key cycles through everything.
    
- **Enter:** Opens the selected file or program.
    
- **`Alt+1` to `Alt+9`:** Opens that numbered row directly, without arrowing down to it. Each row shows its own shortcut on the right.
    
- **Click:** Opens the clicked file or program directly.
    
- **Escape or `Ctrl+Shift+F`:** Hides the search window without closing the app.
    

#### Moving and Resizing the Window

- **Drag the search bar** to move the window anywhere on screen. Pressing on typed text still selects it, so only empty bar space starts a drag.
    
- **Drag the grip** at the right end of the search bar to resize. The whole interface scales together, text, rows and icons included, between $0.7\times$ and $2.0\times$. Double click the grip to return to normal size.
    
- The position and scale are remembered in `~/.filefind_launcher.json`, so the window reopens where you left it, including after a restart.
    
- **Clicking another window does not hide the launcher.** It stays open with whatever you typed still in it. This is deliberate: losing a half typed search to a stray click costs more than tidying itself away is worth. Hiding is always `Escape`, the hotkey, or opening a result.
    

Launcher mode uses `PySide6-Essentials` to draw the window and `keyboard` for the shortcut key. If the shortcut cannot register, the window still opens on launch.

## Project Files

```
FileFind/
├── FileFind.py           Search engine: index, cache and filesystem watcher
├── run_launcher.py       Entry point for the graphical launcher window
├── launcher/             Launcher application package
│   ├── models.py         Data types for queries and results
│   ├── handler.py        Plug in rules and search cancellation
│   ├── dispatcher.py     Runs search providers and ranks final results
│   ├── matcher.py        Shared scoring, so apps and files rank on one scale
│   ├── handlers/         Search provider connectors
│   │   ├── apps.py       Installed Windows application search
│   │   └── files.py      File search wrapping FileFind.py
│   └── ui/               Qt graphical window interface
│       ├── overlay.py    Floating window, moving, scaling, query handling
│       ├── result_view.py Row drawing and file icons
│       ├── shell_icon.py  Icons for Store apps, which have no file on disk
│       └── theme.py      Size metrics, interface scale, color themes
├── tests/                Pytest automated test suite
├── requirements.txt      Project package dependencies
└── .github/workflows/    Automated test configuration
```

`FileFind.py` is the engine and holds no user interface: the search structures, the SQLite cache and the filesystem watcher. The `launcher/` folder adds the window, application discovery, and plug in routing on top of it. `run_launcher.py` is the only entry point.

## Built in Safety and Security

```
Search Query or File Change Event
│
├── Folder Scanning Limits
│   ├── Skips NTFS folder shortcuts and symlinks, so a scan cannot
│   │   escape its intended folder through a junction
│   └── Skips every folder listed in SKIP_DIRECTORIES, at any depth
│
├── Read Only by Design
│   └── FileFind indexes and opens. It never renames, moves or deletes,
│       so a malicious filename has no write path to reach
│
└── Safe Program Launching
    ├── Never runs command line shells (cmd.exe or PowerShell are bypassed)
    ├── Never resolves shortcuts by hand; Windows follows the .lnk itself
    └── Hands files directly to the operating system using os.startfile()
```

FileFind never builds command text strings from filenames, which prevents unusual or malicious filenames from running unwanted commands on your computer.

The strongest protection is what the program does not do. Renaming files, and the filename validation that guarded it, belonged to the terminal application and went with it.

## Automated Testing

FileFind includes unit tests managed through pytest. Currently, all **75 tests** pass cleanly.

### Running Tests

Run all unit tests:

```
pytest
```

Run tests and view code test coverage:

```
pytest --cov=FileFind --cov=launcher
```

### What the Tests Check

- **Path Safety** (`test_path_utils.py`, 7 tests): Verifies safe filename checks, folder escape prevention, illegal Windows character blocking, reserved system name blocking, end of line space rules, name length limits, and drive letter detection.
    
- **Search Engine** (`test_search.py`, 18 tests): Verifies character branches, prefix searches, uppercase or lowercase matching, short name tokens, and that files added to the index are found by search words.
    
- **Launcher** (`test_launcher.py`, 50 tests): Verifies query parsing, keyword routing, search cancellation, one failing connector not breaking the rest, the ranking that keeps applications above similarly named files, and the graphical window itself, including the global shortcut arriving from another thread, `Alt+1` to `Alt+9`, window sizing, interface scaling, moving and remembering position, and the icon cache.
    
- **Isolated Temporary Folders:** Tests use pytest `tmp_path` helpers so they only read and write inside temporary test folders. The window's saved position and the Store application cache are redirected there too, so a test run cannot move your real window or reach for PowerShell.
    

Regression tests are checked by reintroducing the defect and confirming the test fails, not only by watching it pass.

### Areas for Future Tests

Current tests cover the search core, path safety, the SQLite cache, the background writer, the connector system and most of the window. Future tests can be added for:

- Row drawing itself. The paint path is tested for what it must not do, such as touching the disk, but not for what it puts on screen
    
- Fuzzy matching, the fifth search strategy
    
- Exact rank order for files opened many times, rather than only that they are found
    

## Automated Continuous Integration

Every push and pull request to the `main` branch runs tests automatically through GitHub Actions (`.github/workflows/ci.yml`).

Tests run on a `windows-latest` machine across multiple Python versions:

- Python 3.12
    
- Python 3.13
    
- Python 3.14
    

Tests run directly on Windows runners so that Windows specific path features and system names are verified accurately.

## System Summary

|   |   |
|---|---|
|**Area**|**Implementation Detail**|
|**Language**|Python 3.12 and newer with type hints|
|**Core Structures**|Character Trie, Inverted Word Index, Exact Match Dictionary|
|**Saved Cache**|SQLite database with Write Ahead Logging enabled|
|**File Watcher**|Event listener checking Windows folder change signals|
|**Typo Matching**|Word similarity scoring using `rapidfuzz`|
|**Dependencies**|Standard library only for the engine; Qt and `keyboard` for the window|
|**Graphical Window**|`PySide6` frameless search window with `keyboard` shortcut|
|**Opening Files**|Native `os.startfile()` handoff directly to Windows|
|**Testing**|Pytest runner with GitHub Actions workflow on Windows runners|

## License

This project is licensed under the MIT License. See the LICENSE file for details.