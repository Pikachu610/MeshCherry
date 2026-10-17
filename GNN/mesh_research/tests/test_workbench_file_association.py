import unittest

from gis_mesh_workbench.file_association import (
    COMMAND_KEY, DEFAULT_ICON_KEY, EXTENSION_KEY, PROGID, PROGID_KEY,
    install_per_user, uninstall_per_user,
)


class MissingKey(OSError):
    winerror = 2


class FakeRegistry:
    HKEY_CURRENT_USER = object()
    HKEY_CLASSES_ROOT = object()
    KEY_READ = 0x20019
    KEY_SET_VALUE = 0x0002
    KEY_QUERY_VALUE = 0x0001
    KEY_ENUMERATE_SUB_KEYS = 0x0008
    KEY_CREATE_SUB_KEY = 0x0004
    REG_SZ = 1

    def __init__(self):
        self.keys = {}
        self.notifications = 0
        self.writes = []

    class Key:
        def __init__(self, registry, path):
            self.registry, self.path = registry, path

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def OpenKey(self, root, path, reserved=0, access=0):
        if root is self.HKEY_CLASSES_ROOT:
            if path not in (r".mcy", PROGID):
                raise AssertionError(f"HKCR paths must be relative to Classes root: {path!r}")
            path = "HKCR\\" + path
        elif root is not self.HKEY_CURRENT_USER:
            raise AssertionError("unexpected registry root")
        if path not in self.keys:
            raise MissingKey(path)
        return self.Key(self, path)

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        self.keys.setdefault(path, {})
        self.writes.append(("create", path))
        return self.Key(self, path)

    def QueryValueEx(self, key, name):
        try:
            return key.registry.keys[key.path][name], self.REG_SZ
        except KeyError as exc:
            raise MissingKey(key.path) from exc

    def SetValueEx(self, key, name, reserved, value_type, value):
        key.registry.keys[key.path][name] = value
        key.registry.writes.append(("set", key.path, name, value))

    def EnumValue(self, key, index):
        values = list(key.registry.keys[key.path].items())
        try:
            name, value = values[index]
        except IndexError as exc:
            raise OSError("no more values") from exc
        return name, value, self.REG_SZ

    def EnumKey(self, key, index):
        prefix = key.path + "\\"
        children = sorted({path[len(prefix):].split("\\", 1)[0]
                           for path in key.registry.keys if path.startswith(prefix)})
        try:
            return children[index]
        except IndexError as exc:
            raise OSError("no more keys") from exc

    def DeleteValue(self, key, name):
        del key.registry.keys[key.path][name]

    def DeleteKey(self, root, path):
        if path not in self.keys or any(name.startswith(path + "\\") for name in self.keys):
            raise OSError("key is absent or not empty")
        del self.keys[path]


class HierarchicalFakeRegistry(FakeRegistry):
    """Model Windows key creation and the HKCR view of per-user classes."""

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        if root is not self.HKEY_CURRENT_USER:
            raise AssertionError("writes must stay under HKCU")
        parts = path.split("\\")
        for index in range(1, len(parts) + 1):
            self.keys.setdefault("\\".join(parts[:index]), {})
        self.writes.append(("create", path))
        return self.Key(self, path)

    def OpenKey(self, root, path, reserved=0, access=0):
        if root is self.HKEY_CLASSES_ROOT and path == PROGID:
            classes_path = "Software\\Classes\\" + PROGID
            if any(key == classes_path or key.startswith(classes_path + "\\")
                   for key in self.keys):
                return self.Key(self, classes_path)
        return super().OpenKey(root, path, reserved, access)


class FileAssociationTests(unittest.TestCase):
    def test_install_registers_quoted_unicode_path_per_user(self):
        registry = FakeRegistry()
        executable = r"C:\Users\Zoë\Apps\Mesh Cherry\MeshCherry.exe"

        result = install_per_user(executable, winreg_module=registry,
                                  shell_notify=lambda: setattr(registry, "notifications",
                                                               registry.notifications + 1))

        self.assertEqual(registry.keys[EXTENSION_KEY][""], PROGID)
        self.assertEqual(registry.keys[DEFAULT_ICON_KEY][""], f'"{executable}",0')
        self.assertEqual(registry.keys[COMMAND_KEY][""],
                         f'"{executable}" --open-project "%1"')
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["scope"], "current_user")
        self.assertEqual(registry.notifications, 1)
        self.assertTrue(all(path.startswith("Software\\Classes\\")
                            for path in registry.keys))

    def test_refuses_to_take_over_another_extension_default(self):
        registry = FakeRegistry()
        registry.keys[EXTENSION_KEY] = {"": "OtherApp.Project"}

        with self.assertRaisesRegex(ValueError, "already associated"):
            install_per_user(r"C:\Apps\MeshCherry.exe", winreg_module=registry,
                             shell_notify=lambda: self.fail("must not notify on refusal"))

        self.assertEqual(registry.keys[EXTENSION_KEY][""], "OtherApp.Project")
        self.assertEqual(registry.writes, [])

    def test_refuses_to_shadow_machine_wide_extension_default(self):
        registry = FakeRegistry()
        registry.keys["HKCR\\.mcy"] = {"": "MachineOwner.Project"}

        with self.assertRaisesRegex(ValueError, "already associated"):
            install_per_user(r"C:\Apps\MeshCherry.exe", winreg_module=registry,
                             shell_notify=lambda: self.fail("must not notify on refusal"))

        self.assertEqual(registry.writes, [])

    def test_refuses_to_overlay_foreign_effective_progid_namespace(self):
        registry = FakeRegistry()
        registry.keys["HKCR\\" + PROGID] = {"": "Foreign MeshCherry Project"}

        with self.assertRaisesRegex(ValueError, "already associated"):
            install_per_user(r"C:\Apps\MeshCherry.exe", winreg_module=registry,
                             shell_notify=lambda: self.fail("must not notify on refusal"))

        self.assertEqual(registry.writes, [])

    def test_refuses_when_windows_userchoice_pins_another_progid(self):
        registry = FakeRegistry()
        choice_key = (r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"
                      r"\.mcy\UserChoice")
        registry.keys[choice_key] = {"ProgId": "PinnedOtherApp.Project"}

        with self.assertRaisesRegex(ValueError, "Windows currently chooses"):
            install_per_user(r"C:\Apps\MeshCherry.exe", winreg_module=registry,
                             shell_notify=lambda: self.fail("must not notify on refusal"))

        self.assertEqual(registry.writes, [])

    def test_uninstall_removes_only_matching_owned_values_and_empty_keys(self):
        registry = FakeRegistry()
        executable = r"C:\Apps\MeshCherry.exe"
        install_per_user(executable, winreg_module=registry, shell_notify=lambda: None)
        registry.keys[EXTENSION_KEY]["" ] = "OtherApp.Project"
        registry.keys[DEFAULT_ICON_KEY]["custom"] = "foreign-icon"

        result = uninstall_per_user(winreg_module=registry,
                                    shell_notify=lambda: setattr(registry, "notifications",
                                                                 registry.notifications + 1))

        self.assertEqual(registry.keys[EXTENSION_KEY][""], "OtherApp.Project")
        self.assertEqual(registry.keys[DEFAULT_ICON_KEY], {"custom": "foreign-icon"})
        self.assertNotIn("", registry.keys.get(COMMAND_KEY, {}))
        self.assertEqual(result["status"], "uninstalled")
        self.assertEqual(registry.notifications, 1)

    def test_repeated_install_is_idempotent(self):
        registry = FakeRegistry()
        executable = r"C:\Apps\MeshCherry.exe"
        install_per_user(executable, winreg_module=registry, shell_notify=lambda: None)
        registry.writes.clear()

        result = install_per_user(executable, winreg_module=registry,
                                  shell_notify=lambda: self.fail("no association changed"))

        self.assertEqual(result["status"], "already_installed")
        self.assertEqual(registry.writes, [])

    def test_explicit_reinstall_updates_our_icon_and_command_after_app_move(self):
        registry = FakeRegistry()
        install_per_user(r"C:\Old Location\MeshCherry.exe", winreg_module=registry,
                         shell_notify=lambda: None)

        result = install_per_user(r"D:\New Location\MeshCherry.exe",
                                  winreg_module=registry, shell_notify=lambda: None)

        self.assertEqual(registry.keys[DEFAULT_ICON_KEY][""],
                         '"D:\\New Location\\MeshCherry.exe",0')
        self.assertEqual(registry.keys[COMMAND_KEY][""],
                         '"D:\\New Location\\MeshCherry.exe" --open-project "%1"')
        self.assertEqual(result["status"], "installed")

    def test_owned_progid_does_not_shadow_machine_extension_after_user_mapping_removed(self):
        registry = FakeRegistry()
        install_per_user(r"C:\\Apps\\MeshCherry.exe", winreg_module=registry,
                         shell_notify=lambda: None)
        del registry.keys[EXTENSION_KEY][""]
        registry.keys["HKCR\\.mcy"] = {"": "MachineOwner.Project"}
        registry.writes.clear()

        with self.assertRaisesRegex(ValueError, "already associated"):
            install_per_user(r"C:\\Apps\\MeshCherry.exe", winreg_module=registry,
                             shell_notify=lambda: self.fail("must not notify on refusal"))

        self.assertEqual(registry.writes, [])

    def test_uninstall_preserves_foreign_progid_subkeys(self):
        registry = FakeRegistry()
        install_per_user(r"C:\Apps\MeshCherry.exe", winreg_module=registry,
                         shell_notify=lambda: None)
        foreign_key = PROGID + r"\UserSettings"
        full_foreign_key = "Software\\Classes\\" + foreign_key
        registry.keys[full_foreign_key] = {"owner": "OtherApp"}

        uninstall_per_user(winreg_module=registry, shell_notify=lambda: None)

        self.assertEqual(registry.keys[full_foreign_key], {"owner": "OtherApp"})
        self.assertIn("Software\\Classes\\" + PROGID, registry.keys)

    def test_uninstall_does_not_claim_a_lookalike_progid_without_our_marker(self):
        registry = FakeRegistry()
        registry.keys[EXTENSION_KEY] = {"": PROGID}
        registry.keys[DEFAULT_ICON_KEY] = {"": '"C:\\Apps\\MeshCherry.exe",0'}
        registry.keys[COMMAND_KEY] = {"": '"C:\\Apps\\MeshCherry.exe" --open-project "%1"'}
        before = {path: dict(values) for path, values in registry.keys.items()}

        uninstall_per_user(winreg_module=registry, shell_notify=lambda: None)

        self.assertEqual(registry.keys, before)
        self.assertIn(DEFAULT_ICON_KEY, registry.keys)
        self.assertIn(COMMAND_KEY, registry.keys)
        self.assertNotIn(PROGID_KEY, registry.keys)

    def test_uninstall_preserves_association_when_owner_marker_is_mismatched(self):
        registry = FakeRegistry()
        registry.keys[EXTENSION_KEY] = {"": PROGID}
        registry.keys[PROGID_KEY] = {"": "MeshCherry Project",
                                     "MeshCherry.AssociationOwner": "OtherOwner"}
        registry.keys[COMMAND_KEY] = {"": '"C:\\Apps\\MeshCherry.exe" --open-project "%1"'}
        before = {path: dict(values) for path, values in registry.keys.items()}

        uninstall_per_user(winreg_module=registry, shell_notify=lambda: None)

        self.assertEqual(registry.keys, before)

    def test_uninstall_then_reinstall_removes_empty_progid_namespace(self):
        registry = HierarchicalFakeRegistry()
        executable = r"C:\Apps\MeshCherry.exe"

        install_per_user(executable, winreg_module=registry, shell_notify=lambda: None)
        uninstall_per_user(winreg_module=registry, shell_notify=lambda: None)
        progid_path = "Software\\Classes\\" + PROGID
        self.assertFalse(any(path == progid_path or path.startswith(progid_path + "\\")
                             for path in registry.keys))

        result = install_per_user(executable, winreg_module=registry,
                                  shell_notify=lambda: None)

        self.assertEqual(result["status"], "installed")


if __name__ == "__main__":
    unittest.main()
