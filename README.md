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
  - `JWT_SECRET`: Secret for signing JWT access tokens (RECOMMENDED to set)
  - `ACCESS_TOKEN_EXPIRE_DAYS`: Access token expiry in days (default 7)
  - `WORKER_KEY`: Optional shared key to authorize remote worker endpoints

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

## Additive Authentication & Jobs (Multi-user)

These endpoints are additive and do not change existing ones. They enable simple email/password auth and a remote job queue processed by the owner PC.

### Auth
- `POST /auth/signup`
  - Body: `{ "email": string, "password": string }`
  - Creates a new user (password stored as bcrypt hash).
- `POST /auth/login`
  - Body: `{ "email": string, "password": string }`
  - Returns: `{ "access_token": JWT, "user_id": string }`
  - Client must send `Authorization: Bearer <token>` for protected endpoints.

### Scrape Jobs
- `POST /scrape-request` (auth required)
  - Body: `{ "keywords": string[] }`
  - Creates a job with `status=PENDING` bound to the authenticated user.
- `GET /scrape-jobs/pending` (worker only)
  - Returns a single pending job and atomically marks it `IN_PROGRESS`.
  - Must send header `X-Worker-Key: <WORKER_KEY>` if `WORKER_KEY` is set on server.
- `POST /scrape-results` (worker only)
  - Body: `{ job_id, user_id, leads: [...] }`
  - Stores leads into per-user storage and marks job `COMPLETED`.
- `GET /me/leads` (auth required)
  - Returns only the authenticated user's leads.

### Notes
- Existing endpoints `/search`, `/leads`, `/status` remain unchanged for compatibility.
- Set `JWT_SECRET` and `WORKER_KEY` in Render for production security.
