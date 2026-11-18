import unittest

from dms.launcher import phone_stream_url


class PhoneStreamUrlTests(unittest.TestCase):
    def test_phone_address_gets_mjpeg_path(self):
        self.assertEqual(phone_stream_url('192.168.1.1:8080'), 'http://192.168.1.1:8080/video')
        self.assertEqual(phone_stream_url('http://192.168.1.1:8080/'), 'http://192.168.1.1:8080/video')

    def test_explicit_stream_path_is_preserved(self):
        self.assertEqual(phone_stream_url('http://camera:8080/custom'), 'http://camera:8080/custom')

    def test_invalid_input_is_rejected(self):
        for value in ('', 'http://', 'http://user:pass@camera/', 'ftp://camera', 'http://camera:invalid'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                phone_stream_url(value)

class UsbLauncherTests(unittest.TestCase):
    def test_usb_with_ble_option_is_forwarded(self):
        from unittest.mock import patch
        from dms import launcher
        import sys
        with patch.object(sys, 'argv', ['drive', 'usb', '1', '--ble']), \
             patch('dms.launcher.os.chdir'), \
             patch('dms.app.main', return_value=0):
            self.assertEqual(launcher.main(), 0)
            self.assertIn('--ble', sys.argv)
            self.assertEqual(sys.argv[sys.argv.index('--source') + 1], '1')

    def test_usb_ble_prompts_for_index(self):
        from unittest.mock import patch
        from dms import launcher
        import sys
        with patch.object(sys, 'argv', ['drive', 'usb', '--ble']), \
             patch('builtins.input', return_value='2'), \
             patch('dms.launcher.os.chdir'), \
             patch('dms.app.main', return_value=0):
            self.assertEqual(launcher.main(), 0)
            self.assertIn('--ble', sys.argv)
            self.assertEqual(sys.argv[sys.argv.index('--source') + 1], '2')

    def test_usb_camera_number_is_passed_to_application(self):
        from unittest.mock import patch
        from dms import launcher
        import sys
        with patch.object(sys, 'argv', ['drive', 'usb', '1']), \
             patch('dms.launcher.os.chdir'), \
             patch('dms.app.main', return_value=0) as run:
            self.assertEqual(launcher.main(), 0)
            self.assertEqual(sys.argv[-2:], ['--source', '1'])
            run.assert_called_once()

    def test_invalid_camera_number_is_rejected(self):
        from unittest.mock import patch
        from dms import launcher
        import sys
        with patch.object(sys, 'argv', ['drive', 'usb', 'abc']), \
             patch('dms.launcher.os.chdir'):
            self.assertEqual(launcher.main(), 2)

class SavedWifiTests(unittest.TestCase):
    def test_save_reuse_and_update(self):
        import sys
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from dms import launcher
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'configs').mkdir()
            with patch('dms.launcher.project_root', return_value=root), \
                 patch('dms.launcher.os.chdir'), patch('dms.app.main', return_value=0), \
                 patch.object(sys, 'argv', ['drive', 'wifi', '10.0.0.1:8080']):
                self.assertEqual(launcher.main(), 0)
                self.assertEqual(sys.argv[-1], 'http://10.0.0.1:8080/video')
            with patch('dms.launcher.project_root', return_value=root), \
                 patch('dms.launcher.os.chdir'), patch('dms.app.main', return_value=0), \
                 patch('builtins.input', side_effect=AssertionError('unexpected prompt')), \
                 patch.object(sys, 'argv', ['drive', 'wifi']):
                self.assertEqual(launcher.main(), 0)
                self.assertEqual(sys.argv[-1], 'http://10.0.0.1:8080/video')

class WifiBleArgumentTests(unittest.TestCase):
    def test_wifi_forwards_ble_flag_with_saved_address(self):
        import sys
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from dms import launcher
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'configs').mkdir()
            (root / 'configs' / '.wifi-camera').write_text('http://10.0.0.1:8080/video\n')
            with patch('dms.launcher.project_root', return_value=root), patch('dms.launcher.os.chdir'), \
                 patch('dms.app.main', return_value=0), patch.object(sys, 'argv', ['drive', 'wifi', '--ble']):
                self.assertEqual(launcher.main(), 0)
                self.assertIn('--ble', sys.argv)
                self.assertIn('http://10.0.0.1:8080/video', sys.argv)
