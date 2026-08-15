import sys
import os
import logging
import requests
import json
import time
from pathlib import Path
from dotenv import load_dotenv
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel, QLineEdit, QMessageBox, QFrame,
    QScrollArea, QGroupBox, QGridLayout, QFileDialog, QListWidget, QListWidgetItem,
    QRadioButton, QButtonGroup
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format=os.getenv("LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
logger = logging.getLogger(__name__)

# Configuration
DEFAULT_API_URL = os.getenv("API_URL", "http://localhost:8001")
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "240"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "2"))


class ConnectionCheckThread(QThread):
    """Background thread to check backend + Ollama status without blocking the UI."""
    result_signal = pyqtSignal(dict)  # emits {connected, ollama_ok, ollama_message}

    def __init__(self, api_url):
        super().__init__()
        self.api_url = api_url

    def run(self):
        try:
            r = requests.get(f"{self.api_url}/", timeout=5)
            if r.status_code == 200:
                data = r.json()
                self.result_signal.emit({
                    "connected": True,
                    "ollama_ok": data.get("ollama_status") == "ok",
                    "ollama_message": data.get("ollama_message", ""),
                })
                return
        except Exception:
            pass
        self.result_signal.emit({"connected": False, "ollama_ok": False, "ollama_message": ""})


class QueryThread(QThread):
    """Background thread for streaming API queries to prevent UI freezing."""
    token_signal = pyqtSignal(str)      # emitted for each streamed token
    response_signal = pyqtSignal(str)   # emitted when stream completes with full text
    error_signal = pyqtSignal(str)

    def __init__(self, api_url, query, mode="document"):
        super().__init__()
        self.api_url = api_url
        self.query = query
        self.mode = mode
        logger.info(f"QueryThread initialized for streaming")

    def run(self):
        logger.info(f"Starting streaming query: {self.query[:50]}...")
        full_response = ""
        try:
            with requests.post(
                f"{self.api_url}/api/query/stream",
                json={"query": self.query, "mode": self.mode},
                stream=True,
                timeout=API_TIMEOUT
            ) as response:
                response.raise_for_status()
                # Use chunk-based reading to avoid iter_lines() buffering delay
                pending = ""
                done = False
                for chunk in response.iter_content(chunk_size=1):
                    if not chunk or done:
                        continue
                    try:
                        chunk = chunk.decode("utf-8")
                    except Exception:
                        continue
                    pending += chunk
                    # Process all complete SSE lines in the buffer
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            done = True
                            break
                        try:
                            payload = json.loads(data_str)
                            if "error" in payload:
                                self.error_signal.emit(payload["error"])
                                return
                            token = payload.get("token", "")
                            if token:
                                full_response += token
                                self.token_signal.emit(token)
                        except json.JSONDecodeError:
                            continue
            logger.info("Streaming query completed")
            self.response_signal.emit(full_response)
        except requests.exceptions.RequestException as e:
            logger.error(f"Streaming query failed: {e}")
            self.error_signal.emit("Unable to connect to the knowledge base service. Please check if the backend API is running.")


class UploadThread(QThread):
    """Background thread for file uploads to prevent UI freezing."""
    success_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, api_url: str, file_paths: list):
        super().__init__()
        self.api_url = api_url
        self.file_paths = file_paths

    def run(self):
        logger.info(f"Uploading {len(self.file_paths)} file(s)...")
        file_handles = []
        try:
            files = []
            for path in self.file_paths:
                p = Path(path)
                fobj = open(path, "rb")
                file_handles.append(fobj)
                files.append(("files", (p.name, fobj, "application/octet-stream")))
            response = requests.post(
                f"{self.api_url}/api/upload",
                files=files,
                timeout=120
            )
            response.raise_for_status()
            logger.info("Upload successful")
            self.success_signal.emit(response.json())
        except Exception as e:
            logger.error(f"Upload failed: {e}", exc_info=True)
            self.error_signal.emit(str(e))
        finally:
            for fobj in file_handles:
                fobj.close()


class KnowledgeBaseApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.api_url = DEFAULT_API_URL
        self.conversation_history = []
        self.backend_connected = False
        logger.info("Desktop app starting")
        self.init_ui()
        # Initial connection check after UI is fully rendered
        QTimer.singleShot(500, self.check_connection)
        # Periodic recheck every 15 seconds — silent, no popup
        self._recheck_timer = QTimer(self)
        self._recheck_timer.timeout.connect(lambda: self.check_connection(show_popup=False))
        self._recheck_timer.start(15000)

    def init_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle("AI Knowledge Base")
        self.setGeometry(100, 100, 900, 700)
        
        # Set window icon (using absolute path to web app favicon)
        import os
        icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "web_app", "public", "favicon.svg")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # Header
        header = QLabel("🧠 AI Knowledge Base")
        header.setFont(QFont("Arial", 24, QFont.Bold))
        header.setStyleSheet("color: #1f77b4; padding: 10px;")
        header.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(header)
        
        # API URL configuration
        url_layout = QHBoxLayout()
        url_label = QLabel("API URL:")
        url_label.setFont(QFont("Arial", 10))
        self.url_input = QLineEdit(self.api_url)
        self.url_input.setFont(QFont("Arial", 10))
        self.url_input.setText(DEFAULT_API_URL)
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)
        
        # Clear conversation button
        self.clear_button = QPushButton("🗑️ Clear Conversation")
        self.clear_button.setFont(QFont("Arial", 10))
        self.clear_button.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                padding: 5px 10px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
        """)
        self.clear_button.clicked.connect(self.clear_conversation)
        url_layout.addWidget(self.clear_button)
        
        # Connection / Ollama status label
        self.connection_status = QLabel("🔄 Checking...")
        self.connection_status.setFont(QFont("Arial", 10))
        self.connection_status.setStyleSheet("color: #6c757d;")
        url_layout.addWidget(self.connection_status)

        # Connect URL input change to connection check
        self.url_input.textChanged.connect(self.on_url_changed)
        
        main_layout.addLayout(url_layout)
        
        # Conversation section (moved to top)
        response_group = QGroupBox("Conversation")
        response_layout = QVBoxLayout()
        
        # Use scroll area for conversation to handle long content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        
        self.response_display = QTextEdit()
        self.response_display.setReadOnly(True)
        self.response_display.setFont(QFont("Arial", 11))
        self.response_display.setStyleSheet("""
            QTextEdit {
                background-color: #e8f4f8;
                border: 2px solid #1f77b4;
                border-radius: 5px;
                padding: 10px;
            }
        """)
        self.response_display.setMinimumHeight(300)
        scroll.setWidget(self.response_display)
        response_layout.addWidget(scroll)
        
        response_group.setLayout(response_layout)
        main_layout.addWidget(response_group, 1)  # Stretch factor 1 to take available space
        
        # Query section
        query_group = QGroupBox("Ask a Question")
        query_layout = QVBoxLayout()
        
        self.query_input = QTextEdit()
        self.query_input.setPlaceholderText("Enter your question here...")
        self.query_input.setFont(QFont("Arial", 11))
        self.query_input.setMinimumHeight(60)
        self.query_input.setMaximumHeight(80)
        query_layout.addWidget(self.query_input)

        # Mode toggle
        mode_layout = QHBoxLayout()
        mode_label = QLabel("Mode:")
        mode_label.setFont(QFont("Arial", 10, QFont.Bold))
        mode_layout.addWidget(mode_label)
        self._mode_group = QButtonGroup(self)
        self._rb_document = QRadioButton("📄 From My Documents")
        self._rb_document.setFont(QFont("Arial", 10))
        self._rb_document.setToolTip("Answers strictly from uploaded documents")
        self._rb_document.setChecked(True)
        self._rb_assistant = QRadioButton("🤖 Ask AI Freely")
        self._rb_assistant.setFont(QFont("Arial", 10))
        self._rb_assistant.setToolTip("Uses documents as context + AI's own knowledge")
        self._mode_group.addButton(self._rb_document)
        self._mode_group.addButton(self._rb_assistant)
        mode_layout.addWidget(self._rb_document)
        mode_layout.addWidget(self._rb_assistant)
        mode_layout.addStretch()
        query_layout.addLayout(mode_layout)

        self.search_button = QPushButton("🔍 Search")
        self.search_button.setFont(QFont("Arial", 16, QFont.Bold))
        self.search_button.setMinimumHeight(50)
        self.search_button.setMaximumWidth(200)
        self.search_button.setStyleSheet("""
            QPushButton {
                background-color: #1f77b4;
                color: white;
                padding: 15px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #155a8a;
            }
            QPushButton:pressed {
                background-color: #0d3d5c;
            }
        """)
        self.search_button.clicked.connect(self.on_search)
        
        # Center the search button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.search_button)
        button_layout.addStretch()
        query_layout.addLayout(button_layout)
        
        query_group.setLayout(query_layout)
        main_layout.addWidget(query_group)

        # Upload Documents section
        upload_group = QGroupBox("📂 Upload Documents")
        upload_layout = QVBoxLayout()

        self.upload_list = QListWidget()
        self.upload_list.setMaximumHeight(80)
        self.upload_list.setFont(QFont("Arial", 9))
        upload_layout.addWidget(self.upload_list)

        upload_btn_layout = QHBoxLayout()
        self.browse_button = QPushButton("📁 Browse Files")
        self.browse_button.setFont(QFont("Arial", 10))
        self.browse_button.setStyleSheet("""
            QPushButton { background-color: #6c757d; color: white; padding: 6px 12px; border-radius: 4px; }
            QPushButton:hover { background-color: #5a6268; }
        """)
        self.browse_button.clicked.connect(self.browse_files)

        self.upload_button = QPushButton("⬆️ Upload to Knowledge Base")
        self.upload_button.setFont(QFont("Arial", 10))
        self.upload_button.setEnabled(False)
        self.upload_button.setStyleSheet("""
            QPushButton { background-color: #28a745; color: white; padding: 6px 12px; border-radius: 4px; }
            QPushButton:hover { background-color: #218838; }
            QPushButton:disabled { background-color: #adb5bd; }
        """)
        self.upload_button.clicked.connect(self.on_upload)

        upload_btn_layout.addWidget(self.browse_button)
        upload_btn_layout.addWidget(self.upload_button)
        upload_layout.addLayout(upload_btn_layout)

        self.upload_status_label = QLabel("")
        self.upload_status_label.setFont(QFont("Arial", 9))
        self.upload_status_label.setWordWrap(True)
        upload_layout.addWidget(self.upload_status_label)

        upload_group.setLayout(upload_layout)
        main_layout.addWidget(upload_group)

        # Example questions (compact layout)
        example_group = QGroupBox("Example Questions")
        example_layout = QVBoxLayout()        
        examples = [
            "What technologies are used in this pilot stack?",
            "When was the project kickoff date?",
            "What is the purpose of this knowledge base?",
            "How does the RAG system work?"
        ]
        
        # Use grid layout for example buttons (2 columns like web UI)
        example_grid_layout = QGridLayout()
        for i, example in enumerate(examples):
            example_btn = QPushButton(example)
            example_btn.setFont(QFont("Arial", 9))
            example_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f0f2f6;
                    color: #333;
                    padding: 8px;
                    border-radius: 3px;
                    text-align: left;
                }
                QPushButton:hover {
                    background-color: #e0e4ea;
                }
            """)
            # Use functools.partial to avoid lambda closure issue
            from functools import partial
            example_btn.clicked.connect(partial(self.set_query, example))
            row = i // 2
            col = i % 2
            example_grid_layout.addWidget(example_btn, row, col)
        
        example_layout.addLayout(example_grid_layout)
        example_group.setLayout(example_layout)
        main_layout.addWidget(example_group)
        
        # Status bar
        self.statusBar().showMessage("Ready")

    def set_query(self, text):
        """Set the query input with example text."""
        self.query_input.setText(text)

    def browse_files(self):
        """Open file dialog to select documents for upload."""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Documents to Upload",
            "",
            "Supported Files (*.pdf *.txt *.docx *.doc *.md *.csv);;All Files (*)"
        )
        if file_paths:
            self.upload_list.clear()
            self.selected_files = file_paths
            for path in file_paths:
                self.upload_list.addItem(QListWidgetItem(Path(path).name))
            self.upload_button.setEnabled(self.backend_connected)
            self.upload_status_label.setText(f"{len(file_paths)} file(s) selected")
            self.upload_status_label.setStyleSheet("color: #6c757d;")
            logger.info(f"User selected {len(file_paths)} file(s) for upload")

    def on_upload(self):
        """Handle upload button click."""
        if not hasattr(self, 'selected_files') or not self.selected_files:
            QMessageBox.warning(self, "Warning", "Please select files to upload first.")
            return

        if not self.backend_connected:
            QMessageBox.warning(self, "Warning", "Backend is not connected. Please wait for the backend to start.")
            return

        logger.info(f"Starting upload of {len(self.selected_files)} file(s)")
        self.browse_button.setEnabled(False)
        self.upload_button.setEnabled(False)
        self.upload_status_label.setText("Uploading and indexing documents...")
        self.upload_status_label.setStyleSheet("color: #1f77b4;")
        self.statusBar().showMessage("Uploading documents...")

        self.upload_thread = UploadThread(self.api_url, self.selected_files)
        self.upload_thread.success_signal.connect(self.on_upload_success)
        self.upload_thread.error_signal.connect(self.on_upload_error)
        self.upload_thread.start()

    def on_upload_success(self, result):
        """Handle successful upload."""
        self.browse_button.setEnabled(True)
        self.upload_button.setEnabled(True)
        msg = result.get("message", "Upload successful!")
        skipped = result.get("skipped", [])
        self.upload_status_label.setText(f"✅ {msg}")
        self.upload_status_label.setStyleSheet("color: #28a745;")
        self.statusBar().showMessage("Upload completed")
        self.upload_list.clear()
        self.selected_files = []
        self.upload_button.setEnabled(False)
        logger.info(f"Upload success: {msg}")
        if skipped:
            QMessageBox.warning(self, "Skipped Files", f"Unsupported files were skipped:\n{chr(10).join(skipped)}")

    def on_upload_error(self, error_message):
        """Handle upload error."""
        self.browse_button.setEnabled(True)
        self.upload_button.setEnabled(True)
        self.upload_status_label.setText(f"❌ Upload failed: {error_message}")
        self.upload_status_label.setStyleSheet("color: #dc3545;")
        self.statusBar().showMessage("Upload failed")
        logger.error(f"Upload error: {error_message}")
        QMessageBox.critical(self, "Upload Error", f"Upload failed:\n{error_message}")

    def clear_conversation(self):
        """Clear the conversation history."""
        self.conversation_history = []
        self.response_display.clear()
        self.statusBar().showMessage("Conversation cleared")

    def update_conversation_display(self):
        """Update the conversation display with current history."""
        if not self.conversation_history:
            self.response_display.clear()
            return
        
        display_text = ""
        for i, (query, response) in enumerate(self.conversation_history, 1):
            display_text += f"Q{i}: {query}\n\n"
            display_text += f"A{i}: {response}\n\n"
            display_text += "-" * 50 + "\n\n"
        
        self.response_display.setText(display_text)
        # Scroll to bottom
        self.response_display.verticalScrollBar().setValue(
            self.response_display.verticalScrollBar().maximum()
        )

    @staticmethod
    def _ollama_friendly_message() -> str:
        return (
            "Ollama is not running.\n\n"
            "Ollama is the AI engine that powers this application.\n"
            "Please start it before asking questions:\n\n"
            "  • Look for the Ollama icon in your system tray\n"
            "    (bottom-right corner of your taskbar) and click it.\n\n"
            "  • Or open a Command Prompt and type:\n"
            "      ollama serve\n\n"
            "Once Ollama is running, click Search again."
        )

    def check_connection(self, show_popup: bool = True):
        """Spawn a background thread to check backend + Ollama without blocking UI."""
        self._conn_thread = ConnectionCheckThread(self.api_url)
        self._conn_thread.result_signal.connect(
            lambda status: self._apply_connection_status(status, show_popup=show_popup)
        )
        self._conn_thread.start()

    def _apply_connection_status(self, status: dict, show_popup: bool = True):
        """Apply connection check result on the main thread (called via signal)."""
        if not status["connected"]:
            self.backend_connected = False
            self.connection_status.setText("❌ Backend Disconnected")
            self.connection_status.setStyleSheet("color: #dc3545;")
            self.search_button.setEnabled(False)
            return

        self.backend_connected = True
        if status["ollama_ok"]:
            logger.info("Backend and Ollama connected")
            self.connection_status.setText("✅ Connected")
            self.connection_status.setStyleSheet("color: #28a745;")
            self.search_button.setEnabled(True)
        else:
            logger.warning("Backend up but Ollama not running")
            self.connection_status.setText("⚠️ Ollama not running")
            self.connection_status.setStyleSheet("color: #e67e22;")
            self.search_button.setEnabled(False)
            if show_popup:
                QMessageBox.warning(
                    self, "⚠️ Ollama Not Running",
                    self._ollama_friendly_message()
                )

    def on_url_changed(self):
        """Handle URL input change."""
        new_url = self.url_input.text().strip()
        if new_url != self.api_url:
            logger.info(f"API URL changed from {self.api_url} to {new_url}")
            self.api_url = new_url
            self.check_connection()

    def on_search(self):
        """Handle search button click."""
        query = self.query_input.toPlainText().strip()
        
        if not query:
            logger.warning("Empty query submitted")
            QMessageBox.warning(self, "Warning", "Please enter a question.")
            return
        
        self.api_url = self.url_input.text().strip()
        self._current_query = query
        self._streaming_buffer = ""
        logger.info(f"Search initiated with query: {query[:50]}...")
        
        # Disable button and show loading
        self.search_button.setEnabled(False)
        self.search_button.setText("⏳ Receiving...")
        self.statusBar().showMessage("Streaming response...")

        # Append the question header immediately
        idx = len(self.conversation_history) + 1
        self.response_display.append(f"<b>Q{idx}:</b> {query}")
        self.response_display.append(f"<b>A{idx}:</b> ")
        self._stream_cursor_set = False
        
        mode = "assistant" if self._rb_assistant.isChecked() else "document"
        self.query_thread = QueryThread(self.api_url, query, mode=mode)
        self.query_thread.token_signal.connect(self.on_token)
        self.query_thread.response_signal.connect(self.on_response)
        self.query_thread.error_signal.connect(self.on_error)
        self.query_thread.start()

    def on_token(self, token: str):
        """Append a streamed token to the current response line."""
        self._streaming_buffer += token
        cursor = self.response_display.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertText(token)
        self.response_display.setTextCursor(cursor)
        self.response_display.ensureCursorVisible()

    def on_response(self, full_text: str):
        """Handle stream completion."""
        logger.info("Streaming response completed")
        self.search_button.setEnabled(True)
        self.search_button.setText("🔍 Search")
        self.statusBar().showMessage("Search completed")

        # Append separator and save to history
        self.response_display.append("\n" + "-" * 50 + "\n")
        self.conversation_history.append((self._current_query, full_text))
        logger.info(f"Added to conversation history (total: {len(self.conversation_history)})")

        self.response_display.verticalScrollBar().setValue(
            self.response_display.verticalScrollBar().maximum()
        )
        self.query_input.clear()
        # Refresh status label so it stays accurate
        self.check_connection()

    def on_error(self, error_message):
        """Handle API error with Ollama-aware friendly message."""
        logger.error(f"Search error: {error_message}")
        self.search_button.setEnabled(True)
        self.search_button.setText("🔍 Search")
        self.statusBar().showMessage("Search failed")
        # Recheck in background — _apply_connection_status will show the right message
        self._error_recheck = ConnectionCheckThread(self.api_url)
        self._error_recheck.result_signal.connect(self._on_error_recheck)
        self._error_recheck.start()

    def _on_error_recheck(self, status: dict):
        """Show appropriate error message after background recheck."""
        self._apply_connection_status(status)
        if not status.get("ollama_ok"):
            QMessageBox.warning(self, "⚠️ Ollama Not Running", self._ollama_friendly_message())
        else:
            QMessageBox.critical(self, "Error", "Something went wrong. Please try again.")



def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = KnowledgeBaseApp()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
