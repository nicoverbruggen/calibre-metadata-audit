from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

import calibre.customize.ui
from calibre_plugins.metadata_audit import ui


class Preferences(dict):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ToolbarTests(unittest.TestCase):
    def configure(self, prefs):
        bars = Mock()
        action = SimpleNamespace(name='Metadata Audit', gui=SimpleNamespace(bars_manager=bars))
        with patch.object(ui, 'gprefs', prefs):
            ui.MetadataAuditAction.gui_layout_complete(action)
        return bars

    def test_adds_to_both_main_toolbars_without_changing_existing_actions(self):
        prefs = Preferences({'action-layout-toolbar': ('Library', None, 'Preferences'),
                             'action-layout-toolbar-device': ['Library', 'Device']})
        bars = self.configure(prefs)
        self.assertEqual(prefs['action-layout-toolbar'], ['Library', None, 'Preferences', 'Metadata Audit'])
        self.assertEqual(prefs['action-layout-toolbar-device'], ['Library', 'Device', 'Metadata Audit'])
        self.assertTrue(prefs['metadata-audit-toolbar-configured'])
        bars.init_bars.assert_called_once_with()
        bars.update_bars.assert_called_once_with()

    def test_existing_buttons_are_not_duplicated(self):
        prefs = Preferences({'action-layout-toolbar': ['Metadata Audit', 'Library'],
                             'action-layout-toolbar-device': ['Library', 'Metadata Audit']})
        bars = self.configure(prefs)
        self.assertEqual(prefs['action-layout-toolbar'], ['Metadata Audit', 'Library'])
        self.assertEqual(prefs['action-layout-toolbar-device'], ['Library', 'Metadata Audit'])
        bars.init_bars.assert_not_called()
        bars.update_bars.assert_not_called()

    def test_restart_preserves_user_removal_and_custom_order(self):
        prefs = Preferences({'action-layout-toolbar': ['Library'],
                             'action-layout-toolbar-device': ['Device', 'Library']})
        self.configure(prefs)
        prefs['action-layout-toolbar'].remove('Metadata Audit')
        prefs['action-layout-toolbar-device'] = ['Metadata Audit', 'Device', 'Library']
        bars = self.configure(prefs)
        self.assertEqual(prefs['action-layout-toolbar'], ['Library'])
        self.assertEqual(prefs['action-layout-toolbar-device'], ['Metadata Audit', 'Device', 'Library'])
        bars.init_bars.assert_not_called()
        bars.update_bars.assert_not_called()


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ToolbarTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)
