# Leads API

A FastAPI backend for managing leads with status tracking.

## Render Deployment Settings

- **Runtime**: Python 3.11
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn main:app --host 0.0.0.0 --port 10000`
- **Port**: 10000
- **Environment Variables**:
  - `CORS_ALLOW_ORIGINS`: Comma-separated list of allowed origins (defaults to "*")
  - `DATA_DIR`: Directory to store the SQLite database (defaults to current directory)

## Development

1. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the development server:
   ```bash
   uvicorn main:app --reload
   ```

4. The API will be available at `http://localhost:8000`

## API Documentation

- Interactive API docs: `/docs`
- Alternative API docs: `/redoc`
