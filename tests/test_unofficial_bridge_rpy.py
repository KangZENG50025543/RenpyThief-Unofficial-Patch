"""The Ren'Py embed helper must ship with the release and talk to the local Bridge."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RPY = ROOT / "router" / "00unofficial_bridge.rpy"


class UnofficialBridgeRpyTests(unittest.TestCase):
    def test_bridge_script_targets_local_translate(self):
        self.assertTrue(RPY.is_file())
        source = RPY.read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:19899/translate", source)
        self.assertIn("say_menu_text_filter", source)
        self.assertIn("interact_callbacks", source)
        self.assertNotIn("sk-", source)


if __name__ == "__main__":
    unittest.main()
