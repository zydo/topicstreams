"""Shared test setup.

Importing the app modules instantiates `common.settings.Settings()`, which
requires POSTGRES_PASSWORD. Provide a dummy so imports succeed; the unit tests
never open a connection.
"""

import os

os.environ.setdefault("POSTGRES_PASSWORD", "test")
