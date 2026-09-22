"""SQLite held to the adapter contract (NFR-2).

NFR-3 asks for this same suite to run against the production adapter in CI.
That is not satisfied yet: there is no Oracle adapter and no pipeline pointing
at one. When both exist, the Oracle suite is a subclass of RepositoryContract,
not a second copy of it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from isrp.orchestration import SQLiteWorkflowRepository

from contract import RepositoryContract


class SQLiteContractTests(RepositoryContract, unittest.TestCase):
    def make_repository(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        repository = SQLiteWorkflowRepository(Path(self.temp.name) / "contract.db")
        repository.initialize()
        return repository


if __name__ == "__main__":
    unittest.main()
