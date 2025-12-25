# File Commander

**Python 3.10+** | **License: MIT**

Smart file operations made simple. File Commander is an interactive CLI tool that helps users efficiently find, create, and manage files with intelligent search capabilities and confidence-based matching.

## ✨ Features

- **📁 Create Files & Folders** - Create new folders and files with nested structures
- **⚡ Search & Manage Files/Folders** - Lightning-fast search with open and rename operations
- **📋 List Directory Contents** - Browse and explore folder contents with filtering
- **⚙️ Search Statistics** - View search index status and performance metrics

### Core Capabilities

- **Intelligent Search Engine**: Multi-strategy search using Trie data structure for instant results
- **Confidence-Based Matching**: Prevents false matches with relevance scoring
- **Incremental Indexing**: Progressive folder scanning with early termination for optimal performance
- **Continuous Search**: Search multiple times without re-indexing overhead
- **Secure Operations**: Path validation and input sanitization to prevent security vulnerabilities
- **Integrated Undo**: Immediate undo option for rename operations
- **Professional UI**: Rich terminal interface with tables, panels, and visual feedback

## 📋 Prerequisites

- **Python 3.10+** (supports modern syntax and type hints)
- **pip** (Python package manager)

## 🚀 Installation

1. **Clone this repository:**

```bash
git clone https://github.com/yourusername/file-commander.git
cd file-commander
```

2. **Create a virtual environment (recommended):**

```bash
# On Windows
python -m venv venv
venv\Scripts\activate
```

3. **Install required dependencies:**

```bash
pip install -r requirements.txt
```

## 🖥 Usage

Start File Commander with:

```bash
python file-commander.py
```

### Interactive Menu

⚡ File Commander
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 Choose an Operation

| Option | Description                      |
| ------ | -------------------------------- |
| 1      | 📁 Create Files & Folders        |
| 2      | ⚡ Search & Manage Files/Folders |
| 3      | 📋 List Directory Contents       |
| 4      | ⚙️ Search Statistics             |
| 0      | ❌ Exit                          |

### Key Features in Action

**⚡ Continuous File Search:**

- Index once, search multiple times
- No re-indexing overhead
- Instant results with multiple search strategies
- Open, rename, or search again seamlessly

**📁 Secure File Operations:**

- Input validation prevents directory traversal attacks
- Safe filename checking
- Integrated undo for rename operations

## 🔧 Technical Architecture

### Search Engine

- **Trie Data Structure**: Fast prefix matching for autocomplete-style search
- **Multi-Strategy Search**: Exact match, prefix, word-based, and substring algorithms
- **Relevance Scoring**: Results sorted by match quality and location
- **Incremental Indexing**: Process folders progressively with early termination

### Security Features

- **Path Traversal Protection**: Prevents `../` attacks
- **Input Sanitization**: Validates all user-provided filenames
- **Safe File Operations**: Comprehensive error handling for all file system operations

### Performance Optimizations

- **Smart Drive Indexing**: C: drive indexes user folders only; other drives index completely
- **Priority-Based Scanning**: Checks likely locations first
- **Memory-Efficient Indexing**: Smart caching without memory bloat
- **Zero Re-indexing**: Continuous search without performance penalties

## 🎯 Why Choose File Commander?

### Speed & Efficiency

- **Progressive Search**: No waiting for complete system indexing
- **Continuous Workflows**: Multiple operations without restarting

### Smart & Reliable

- **Relevance-Based Matching**: Finds the right files with smart scoring
- **Security-First Design**: Input validation prevents common vulnerabilities
- **Professional Interface**: Rich terminal UI with clear visual feedback

### User-Friendly

- **Intuitive Menus**: Clear, numbered options for all operations
- **Integrated Workflows**: Create files and immediately open them
- **Helpful Feedback**: Clear success/error messages with visual indicators
- **Undo Support**: Immediate undo for rename operations

File Commander bridges the gap between simple file browsers and complex command-line tools, offering powerful search capabilities in an accessible, menu-driven interface.

## 📦 Dependencies

```txt
rich>=14.0.0     # Terminal UI (prompts, tables, panels)
```

## 🛠 Troubleshooting

- **Slow initial search**: First-time indexing of large folders may take a moment
- **No results found**: Try broader search terms or check file locations
- **Permission errors**: Ensure you have read access to the directories being searched
- **Memory usage**: Large file collections may require more RAM for indexing

## 📜 License

This project is open-source and available under the MIT License.

## 🙏 Acknowledgements

- **Rich** for beautiful terminal output, interactive prompts, and elegant components
