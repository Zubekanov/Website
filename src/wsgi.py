from app import create_app

# Gunicorn entrypoint: `wsgi:app` (with WorkingDirectory=/.../src)
app = create_app()
