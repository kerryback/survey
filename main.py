"""Entry point. Koyeb's buildpack and the Dockerfile both run `main:app`."""

from app.main import app

__all__ = ["app"]
