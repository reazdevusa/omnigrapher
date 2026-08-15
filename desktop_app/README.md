# AI Knowledge Base - Desktop Application

A native cross-platform desktop application for the AI Knowledge Base system built with PyQt5.

## Features
- Native desktop experience with modern UI
- Natural language query interface
- Real-time responses from your knowledge base
- Configurable API endpoint
- Example questions for quick testing
- Background processing to prevent UI freezing

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Ensure the backend API is running:
```bash
cd ../knowledge_base_pilot
uvicorn app.main:app --reload
```

3. Run the desktop application:
```bash
cd src
python main.py
```

## Configuration
- Default API URL: `http://localhost:8000`
- Can be changed via the API URL input field

## Usage
1. Enter your question in the text area
2. Click "Search" to query the knowledge base
3. View the AI-generated response in the response area
4. Use example questions for quick testing

## Technology Stack
- **GUI Framework**: PyQt5
- **Backend**: FastAPI (separate service)
- **Communication**: REST API
- **Platform**: Cross-platform (Windows, macOS, Linux)

## Building Executable
To create a standalone executable:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed src/main.py
```

The executable will be created in the `dist` folder.
