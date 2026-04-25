"""Main application entry point for Jingxin-Agent.

This is the primary entry point for the application.
Run with: python3 app.py or uvicorn app:app
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'backend'))

from api.app import app, main

__all__ = ["app", "main"]

if __name__ == "__main__":
    main()
