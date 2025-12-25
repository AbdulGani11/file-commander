# ⚡ File Commander

[![CI](https://github.com/AbdulGani11/file-commander/actions/workflows/ci.yml/badge.svg)](https://github.com/AbdulGani11/file-commander/actions)
![Platform Windows](https://img.shields.io/badge/platform-Windows-blue.svg)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

**Smart operations made simple.** A high-performance, offline CLI tool designed for **Windows** that helps you manage files with sub-second search speeds and a professional terminal interface.

---

## 🚀 Key Features

*   **⚡ Instant Search**: Uses **Trie Data Structures** (Prefix Trees) for O(L) lookup speed. Finds files instantly even in massive directories.
*   **🎨 Professional UI**: Built with `Rich` for a modern, centering layout with gradients, icons, and responsive tables.
*   **🛡️ Security-First**: Input sanitization and path traversal protection prevent accidental filesystem damage.
*   **🧠 Smart Caching**: Indexes folders once and caches the result in memory for continuous, lag-free operations.
*   **🧪 Battle-Tested**: 100% unit test coverage with automated CI/CD pipelines on GitHub Actions.

## �️ Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/AbdulGani11/file-commander.git
    cd file-commander
    ```

2.  **Set up environment:**
    ```bash
    python -m venv venv
    # Windows:
    venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## 🖥️ Usage

Run the application:
```bash
python file-commander.py
```

### 📋 Interactive Menu
The application features a keyboard-driven menu system:
1.  **📁 Create**: Generate files and tailored folder structures.
2.  **⚡ Search**: Smart fuzzy-search for files (supports opening and renaming).
3.  **📋 List**: Browse directories with ease.
4.  **⚙️ Stats**: View indexing performance and memory usage.

## 🧪 Development & Testing

This project adheres to professional engineering standards.

**Run Unit Tests:**
```bash
# Run the full test suite
pytest

# Run with coverage report
pytest --cov=file-commander
```

**Continuous Integration:**
Every push triggers a GitHub Actions workflow that runs tests across Python 3.10, 3.11, and 3.12 to ensure cross-platform compatibility.

## � Technical Architecture

*   **Core**: Python 3.10+ (Type Hinted)
*   **Algorithms**: Prefix Trie, Depth-First Search (DFS)
*   **UI Framework**: Rich (Textual formatting)
*   **Testing**: Pytest, GitHub Actions

## 📜 License

MIT License © 2025 Abdul Gani
