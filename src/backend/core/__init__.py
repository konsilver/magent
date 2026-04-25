"""Core package.

Keep package import side-effect free:
- Do not import submodules here.
- Import from concrete modules, e.g. `from core.chat.session import get_session_store`.
"""

__all__ = []
