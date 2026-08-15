"""Static checks for the public-release asset contract."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.2"
PORTABLE_NAME = f"RenpyThiefPatch-v{VERSION}-portable-x64"
INSTALLER_NAME = f"RenpyThiefPatch-v{VERSION}-setup-x64.exe"


class ReleaseMetadataTests(unittest.TestCase):
    def test_python_package_version_matches_release(self):
        source = (ROOT / "src" / "renpy_patch" / "__init__.py").read_text(
            encoding="utf-8"
        )
        match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), VERSION)

    def test_build_script_uses_public_asset_names(self):
        source = (ROOT / "build_release.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("[string]$Version = '1.0.2'", source)
        self.assertIn('"RenpyThiefPatch-v$Version-portable-x64"', source)
        self.assertIn('"RenpyThiefPatch-v$Version-setup-x64.exe"', source)
        self.assertIn("packaging\\QUICK_START.txt", source)
        self.assertIn("$localizedLauncherName", source)
        codepoints = re.findall(r"\[char\]0x([0-9A-Fa-f]{4})", source)
        self.assertEqual(
            "".join(chr(int(value, 16)) for value in codepoints[:7]) + ".cmd",
            "启动非官方补丁.cmd",
        )

    def test_installer_builder_agrees_with_release_name(self):
        source = (ROOT / "scripts" / "build_installer.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn(INSTALLER_NAME, source)

        installer = (ROOT / "packaging" / "installer.iss").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn(f'#define MyAppVersion "{VERSION}"', installer)
        self.assertIn(f"OutputBaseFilename={INSTALLER_NAME[:-4]}", installer)
        self.assertIn(f"VersionInfoVersion={VERSION}.0", installer)

    def test_quick_start_and_launchers_are_present(self):
        self.assertTrue((ROOT / "packaging" / "QUICK_START.txt").is_file())
        launcher = (ROOT / "packaging" / "LaunchPatch.cmd").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("RenpyThiefPatch.exe", launcher)

    def test_release_names_are_distinct(self):
        self.assertNotEqual(PORTABLE_NAME + ".zip", INSTALLER_NAME)

    def test_user_facing_release_documents_match_version(self):
        quick_start = (ROOT / "packaging" / "QUICK_START.txt").read_text(
            encoding="utf-8-sig"
        )
        release_notes = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8-sig")
        self.assertIn(f"v{VERSION}", quick_start.splitlines()[0])
        self.assertTrue(release_notes.startswith(f"# v{VERSION} "))
        self.assertIn(f"`{INSTALLER_NAME}`", release_notes)
        self.assertIn(f"`{PORTABLE_NAME}.zip`", release_notes)


if __name__ == "__main__":
    unittest.main()
