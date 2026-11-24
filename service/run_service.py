"""Entry point for the bundled service (PyInstaller can't start from a package's __main__.py)."""

import presentation_service.__main__  # noqa: F401  (runs the service)
