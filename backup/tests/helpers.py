import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings


class TempBackupDirMixin:
    """Mixin that redirects BACKUP_DIR to a temp directory for test isolation.

    Provides self.backup_dir (Path) pointing to the isolated temp directory.
    The mixin patches settings.BACKUP_DIR and cleans up everything on tearDown,
    so individual test classes don't need to glob-delete archives.

    Place this mixin BEFORE TestCase in the class bases so its setUp/tearDown
    wrap correctly.
    """

    def setUp(self):
        self._backup_tmpdir_obj = tempfile.mkdtemp(prefix="nodered_test_backups_")
        self.backup_dir = Path(self._backup_tmpdir_obj)
        self._patcher = patch.object(settings, "BACKUP_DIR", self.backup_dir)
        self._patcher.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self._patcher.stop()
        shutil.rmtree(self._backup_tmpdir_obj, ignore_errors=True)


SAMPLE_FLOWS = [
    {"id": "tab1", "type": "tab", "label": "Home Automation"},
    {"id": "tab2", "type": "tab", "label": "API Endpoints"},
    {"id": "sf1", "type": "subflow", "name": "Error Handler"},
    {"id": "g1", "type": "group", "name": "Sensors", "z": "tab1"},
    {
        "id": "n1",
        "type": "inject",
        "z": "tab1",
        "g": "g1",
        "name": "Trigger",
        "x": 100,
        "y": 200,
    },
    {"id": "n2", "type": "debug", "z": "tab1", "name": "Log"},
    {"id": "n3", "type": "http in", "z": "tab2"},
    {"id": "n4", "type": "function", "z": "sf1"},
    {"id": "cfg1", "type": "mqtt-broker"},  # no z → config node
]
