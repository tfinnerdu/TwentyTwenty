"""
wsgi.py -- production WSGI entry point.

The Flask application object lives in api/app.py. Gunicorn (and Render) import it
from here so the start command is a stable `gunicorn wsgi:app`, independent of the
package layout. `gunicorn api.app:app` works too.

Note: Render's Python autodetect defaults the start command to `gunicorn app:app`,
which fails here because there is no top-level `app` module -- point the service's
Start Command at `wsgi:app` (or `api.app:app`).
"""
from api.app import app  # re-exported for gunicorn: `gunicorn wsgi:app`

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5902")))
