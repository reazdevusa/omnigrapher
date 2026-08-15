# AI Knowledge Base - Web Application

A modern, responsive web interface for the AI Knowledge Base system built with Streamlit.

## Features
- Natural language query interface
- Real-time responses from your knowledge base
- Clean, modern UI with custom styling
- Example questions for quick testing
- Configurable API endpoint

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

3. Run the web application:
```bash
streamlit run app.py
```

The application will open in your browser at `http://localhost:8501`

## Configuration
- Default API URL: `http://localhost:8000`
- Can be changed via the sidebar settings

## Usage
1. Enter your question in the text area
2. Click "Search" to query the knowledge base
3. View the AI-generated response
4. Use example questions for quick testing

## Technology Stack
- **Frontend**: Streamlit
- **Backend**: FastAPI (separate service)
- **Communication**: REST API
