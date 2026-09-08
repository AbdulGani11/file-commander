# FileFind

FileFind is a fast local file search tool and explorer for Windows. It replaces slow and unpredictable system search with fast in memory search trees, word indexes, a saved SQLite database cache, and a background watcher that tracks file changes instantly.

Search queries run with $O(m)$ prefix speed, where $m$ is the number of letters in your search word. This means searching takes the same small fraction of time whether you have ten files or a hundred thousand files on your computer.

## Main Features

- **Five Step Search System:** Looks for files using five methods in order: exact filename match, prefix tree lookup, word index search, substring scan, and optional typo correction.
    
- **Fast Startup on Later Runs:** Saves index data to an SQLite database file at `~/.filefind_cache.db`. Starting the app later loads over $124{,}000$ files in under $400$ milliseconds, skipping the long $10$ to $60$ second folder scan completely.
    
- **Live Background Updates:** A background file listener monitors your folders and sends file creation, deletion, and rename events to a background worker thread using a safe queue.
    
- **Application Launcher:** Finds installed programs from three places Windows uses: Start Menu shortcuts, the Windows registry `App Paths` key, and programs located in your system `PATH`. Short name matching also lets you find tools quickly, such as typing `vsc` to find Visual Studio Code.
    
- **Floating Search Window:** A quick launcher mode keeps the index ready in memory. Pressing `Ctrl+Shift+F` opens a clean, borderless search window right on top of your screen, letting you find files without leaving your active work.
    
- **Modular Search Connectors:** The search system uses simple plug in connectors. Files, installed apps, and future search sources combine into one sorted results list. If you type new letters, earlier background searches cancel immediately so your computer stays fast.
    
- **Learns What You Open Most:** Tracks how many times you open each file inside `~/.filefind_history.json`. It gives a bonus of up to $+40$ points to your favorite files so they show up at the top of your search results.
    
- **Smart Folder Scanning:** Indexes important user folders on your main C: drive while scanning secondary drives and USB storage completely.
    
- **Built in Safety and Security:** Blocks unsafe folder path tricks, reserved Windows system names, invalid Windows letters, and folder shortcut loops.
    

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

The command line tool only needs `rich` to run. Other tools are optional, and FileFind continues running safely even if an optional package is missing. The graphical floating window mode needs the packages marked **Launcher** in the table below.

|   |   |   |   |
|---|---|---|---|
|**Package**|**Minimum Version**|**Requirement**|**What Is Lost If Missing**|
|`rich`|`>= 15.0.0`|Required|The command line screen cannot display without it|
|`watchdog`|`>= 6.0.0`|Optional|Live background file change tracking|
|`rapidfuzz`|`>= 3.14.6`|Optional|Typo correction and "Did you mean?" suggestions|
|`keyboard`|`>= 0.13.5`|Launcher|Global hotkey to open the floating window|
|`PySide6-Essentials`|`>= 6.11.2`|Launcher|Drawing the graphical search window|
|`pytest`|`>= 9.1.1`|Development|Running automated tests|
|`pytest-cov`|`>= 7.1.0`|Development|Creating test coverage reports|

The command line application checks optional packages on startup and sets status flags (`WATCHDOG_AVAILABLE`, `RAPIDFUZZ_AVAILABLE`). The core search engine works completely with only `rich` installed.

The two packages marked **Launcher** are only needed when running the graphical window (`run_launcher.py`). The terminal tool does not load them.

## How to Use FileFind

FileFind gives you two ways to work.

### 1. Interactive Terminal Mode

Launch the terminal menu:

```
python FileFind.py
```

#### Main Menu Choices

- **`1` Search:** Enter search words to find files. Displays ranked matches with index numbers, names, file types, and locations.
    
- **`2` Statistics:** Shows index status, total file count, database cache location, file size, and background file watcher status.
    
- **`3` Refresh Index:** Deletes the saved cache file and scans all folders again from scratch.
    
- **`0` Exit:** Stops background listeners and closes cleanly.
    

#### What You Can Do With a Result

After picking a search result number, you can:

- **Open:** Opens the file or folder using its standard Windows program.
    
- **Rename:** Prompts for a new name with security checks, and offers an instant undo option.
    
- **New Search:** Clears current results and starts a new search.
    
- **Return:** Goes back to the main menu.
    

### 2. Floating Launcher Window Mode

Run FileFind as a background launcher with a global hotkey and a graphical pop up window:

```
python run_launcher.py                  # Scans this project folder only (fast for testing)
python run_launcher.py --full           # Scans standard user folders
python run_launcher.py --theme Light    # Pick a color theme: Dark, Darker, or Light
```

Launcher mode indexes your applications and files, turns on the background file watcher, sets up the `Ctrl+Shift+F` shortcut, and waits in the background. Pressing the shortcut opens a borderless search box in the center of your screen.

Because the index is already loaded in computer memory, opening the search box is instant.

#### Window Controls

- **Type:** Results update live as you type, showing up to $8$ ranked matches.
    
- **Up and Down arrows:** Move through the results list.
    
- **Enter:** Opens the selected file or program.
    
- **Double click:** Opens the clicked file or program directly.
    
- **Escape:** Hides the search window without closing the app.
    

Launcher mode uses `PySide6-Essentials` to draw the window and `keyboard` for the shortcut key. If the shortcut cannot register, the window still opens on launch, and the terminal tool continues to work normally.

## Project Files

```
FileFind/
├── FileFind.py           Search engine and interactive terminal interface
├── run_launcher.py       Entry point for the graphical launcher window
├── launcher/             Launcher application package
│   ├── models.py         Data types for queries and results
│   ├── handler.py        Plug in rules and search cancellation
│   ├── dispatcher.py     Runs search providers and ranks final results
│   ├── handlers/         Search provider connectors
│   │   ├── apps.py       Installed Windows application search
│   │   └── files.py      File search wrapping FileFind.py
│   └── ui/               Qt graphical window interface
│       ├── overlay.py    Floating window and query handling
│       ├── result_view.py Row drawing and file icons
│       └── theme.py      Size metrics and color themes
├── tests/                Pytest automated test suite
├── requirements.txt      Project package dependencies
└── .github/workflows/    Automated test configuration
```

Both entry points share the same search core. `FileFind.py` holds the search structures and terminal menus. The `launcher/` folder adds window controls, app discovery, and plug in routing on top of the search engine.

## Built in Safety and Security

```
User Input or File Change Event
│
├── Input Cleaning and Validation
│   ├── Rejects blank names and spaces
│   ├── Blocks folder escape tricks ("..", "\", "/")
│   ├── Limits filename length to 255 characters
│   └── Blocks reserved Windows device names (CON, PRN, AUX, NUL, COM1..9, LPT1..9)
│
├── Folder Boundary Check
│   └── Verifies: new_path.parent.resolve() == original_path.parent.resolve()
│
├── Folder Scanning Limits
│   ├── Skips NTFS folder shortcuts and symlinks
│   └── Skips folders listed in SKIP_DIRECTORIES
│
└── Safe Program Launching
    ├── Never runs command line shells (cmd.exe or PowerShell are bypassed)
    └── Hands files directly to the operating system using os.startfile()
```

FileFind never builds command text strings from filenames, which prevents unusual or malicious filenames from running unwanted commands on your computer.

## Automated Testing

FileFind includes unit tests managed through pytest. Currently, all **7 tests** pass cleanly.

### Running Tests

Run all unit tests:

```
pytest
```

Run tests and view code test coverage:

```
pytest --cov=FileFind
```

### What the Tests Check

- **Path Safety** (`test_path_utils.py`, 3 tests): Verifies safe filename checks, folder escape prevention, illegal Windows character blocking, reserved system name blocking, end of line space rules, name length limits, and drive letter detection.
    
- **Prefix Tree Operations** (`test_search.py`, 2 tests): Verifies character branches, prefix searches, preventing unrelated matches, and uppercase or lowercase matching.
    
- **Index Searching** (`test_search.py`, 2 tests): Verifies that files added to the index are found by search words and that multiple matching files are returned.
    
- **Isolated Temporary Folders:** Tests use pytest `tmp_path` helpers so they only read and write inside temporary test folders.
    

### Areas for Future Tests

Current tests cover the search core and path safety rules. Future tests can be added for:

- Saving and loading the SQLite database file
    
- Removing files and clearing old search tree links
    
- The background file worker thread
    
- The graphical search window and row drawing
    
- Typo matching and spelling suggestions
    
- Verifying exact rank order for files opened multiple times
    

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
|**Terminal Display**|Rich tables, status indicators, and input menus|
|**Graphical Window**|`PySide6` frameless search window with `keyboard` shortcut|
|**Opening Files**|Native `os.startfile()` handoff directly to Windows|
|**Testing**|Pytest runner with GitHub Actions workflow on Windows runners|

## License

This project is licensed under the MIT License. See the LICENSE file for details.