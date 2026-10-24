"""Exercise retained migration mechanics under an explicitly simulated old build.

These tests are not evidence that a current build can migrate an old protocol.
test_delivery separately checks the real current build rejects those upgrades.
"""
from unittest.mock import patch
from runtime.transport_repair import WORK_CONTRACT


class LegacyMigrationFixture:
    def setUp(self):
        super().setUp()
        for setting in (patch('runtime.core.VERSION', '1.3.1'),
                        patch('runtime.transport_repair.work_contract', return_value=WORK_CONTRACT)):
            setting.start()
            self.addCleanup(setting.stop)
