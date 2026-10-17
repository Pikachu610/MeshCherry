"""Explicit, reversible per-user registration for MeshCherry `.mcy` files.

This module never runs registration at import time. Callers should invoke
``install_per_user`` or ``uninstall_per_user`` only from an explicit user
action. All writes are limited to HKCU\\Software\\Classes. The effective
classes view and UserChoice are read only; HKLM and UserChoice are never modified.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re


CLASSES_ROOT = r"Software\Classes"
EXTENSION_KEY = CLASSES_ROOT + r"\.mcy"
PROGID = "MeshCherry.Project"
PROGID_KEY = CLASSES_ROOT + "\\" + PROGID
DEFAULT_ICON_KEY = PROGID_KEY + r"\DefaultIcon"
SHELL_KEY = PROGID_KEY + r"\shell"
OPEN_KEY = PROGID_KEY + r"\shell\open"
COMMAND_KEY = OPEN_KEY + r"\command"
_OWNER_VALUE = "MeshCherry.AssociationOwner"
_OWNER_MARKER = "MeshCherry per-user mcy association v1"
_OWNED_COMMAND = re.compile(r'^"[^"\r\n]+" --open-project "%1"$')


def _winreg(module=None):
    if module is not None:
        return module
    try:
        import winreg
    except ImportError as exc:
        raise RuntimeError("MeshCherry file association is available only on Windows") from exc
    return winreg


def _notify_shell():
    """Tell Explorer that a per-user association changed."""
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except (AttributeError, OSError) as exc:
        raise RuntimeError("Windows shell association notification failed") from exc


def _missing(exc):
    return isinstance(exc, FileNotFoundError) or getattr(exc, "winerror", None) in (2, 3)


def _read_value(registry, path, name="", *, root=None):
    root = registry.HKEY_CURRENT_USER if root is None else root
    try:
        with registry.OpenKey(root, path, 0, registry.KEY_READ) as key:
            return registry.QueryValueEx(key, name)[0]
    except OSError as exc:
        if _missing(exc):
            return None
        raise


def _key_exists(registry, path, *, root):
    try:
        with registry.OpenKey(root, path, 0, registry.KEY_READ):
            return True
    except OSError as exc:
        if _missing(exc):
            return False
        raise


def _set_value(registry, path, name, value):
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, path, 0,
                              registry.KEY_SET_VALUE) as key:
        registry.SetValueEx(key, name, 0, registry.REG_SZ, value)


def _delete_value(registry, path, name, expected):
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, path, 0,
                              registry.KEY_QUERY_VALUE | registry.KEY_SET_VALUE) as key:
            try:
                value = registry.QueryValueEx(key, name)[0]
            except OSError as exc:
                if _missing(exc):
                    return False
                raise
            if value != expected:
                return False
            registry.DeleteValue(key, name)
            return True
    except OSError as exc:
        if _missing(exc):
            return False
        raise


def _key_is_empty(registry, path):
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, path, 0,
                              registry.KEY_QUERY_VALUE | registry.KEY_ENUMERATE_SUB_KEYS) as key:
            for enum_value in (registry.EnumValue, registry.EnumKey):
                try:
                    enum_value(key, 0)
                except OSError as exc:
                    if getattr(exc, "winerror", None) not in (None, 259):
                        raise
                else:
                    return False
            return True
    except OSError as exc:
        if _missing(exc):
            return False
        raise


def _delete_if_empty(registry, path):
    if not _key_is_empty(registry, path):
        return False
    try:
        registry.DeleteKey(registry.HKEY_CURRENT_USER, path)
        return True
    except OSError as exc:
        if _missing(exc):
            return False
        # A key can become nonempty between enumeration and deletion; preserve it.
        if getattr(exc, "winerror", None) in (5, 32, 145):
            return False
        raise


def _registration_values(executable):
    raw = os.fspath(executable)
    if not isinstance(raw, str) or not raw or any(char in raw for char in '"\r\n\0'):
        raise ValueError("Executable path must be a nonempty Windows path without quotes")
    path = str(Path(raw).expanduser().resolve(strict=False))
    if not Path(path).is_absolute():
        raise ValueError("Executable path must be absolute")
    return {
        (PROGID_KEY, ""): "MeshCherry Project",
        (PROGID_KEY, _OWNER_VALUE): _OWNER_MARKER,
        (DEFAULT_ICON_KEY, ""): f'"{path}",0',
        (COMMAND_KEY, ""): f'"{path}" --open-project "%1"',
        # Publish the extension mapping last, after its target is complete.
        (EXTENSION_KEY, ""): PROGID,
    }


def install_per_user(executable, *, winreg_module=None, shell_notify=None):
    """Install the `.mcy` association for the current Windows user only.

    Existing `.mcy` defaults owned by another application are never replaced.
    Returns a small status object suitable for an explicit installer CLI.
    """
    registry = _winreg(winreg_module)
    desired = _registration_values(executable)
    owns_progid = _read_value(registry, PROGID_KEY, _OWNER_VALUE) == _OWNER_MARKER
    hkcr = getattr(registry, "HKEY_CLASSES_ROOT", None)
    if hkcr is not None:
        # HKCR is the effective merged classes view. Refuse to shadow a
        # machine-wide handler, even when this user's ProgID marker survived
        # after their per-user extension mapping was removed.
        effective_extension = _read_value(registry, r".mcy", root=hkcr)
        if effective_extension not in (None, "", PROGID):
            raise ValueError("The .mcy extension is already associated with another application")
        # Our own per-user ProgID is visible through HKCR too. Only treat an
        # existing merged namespace as foreign when we do not own that key.
        if not owns_progid and _key_exists(registry, PROGID, root=hkcr):
            raise ValueError("The MeshCherry ProgID namespace is already associated with another application")
    choice_key = (r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"
                  r"\.mcy\UserChoice")
    user_choice = _read_value(registry, choice_key, "ProgId")
    if user_choice not in (None, "", PROGID):
        raise ValueError("Windows currently chooses another application for .mcy; change it in Windows Settings first")
    # Preflight all values before creating keys so a conflict cannot leave a
    # half-installed registration behind.
    for (path, name), value in desired.items():
        current = _read_value(registry, path, name)
        if current not in (None, "", value):
            if path != EXTENSION_KEY and owns_progid:
                # Explicit reinstall after moving the application may update
                # only the ProgID values already proven to be ours.
                continue
            raise ValueError("The .mcy extension or MeshCherry ProgID is already associated with another application")
    changed = []
    for (path, name), value in desired.items():
        if _read_value(registry, path, name) == value:
            continue
        _set_value(registry, path, name, value)
        changed.append(path if not name else f"{path}\\{name}")
    if changed:
        (shell_notify or _notify_shell)()
        status = "installed"
    else:
        status = "already_installed"
    return {"status": status, "scope": "current_user", "extension": ".mcy",
            "progid": PROGID, "changed": changed}


def uninstall_per_user(*, winreg_module=None, shell_notify=None):
    """Remove only recognizable MeshCherry association values and empty keys."""
    registry = _winreg(winreg_module)
    if _read_value(registry, PROGID_KEY, _OWNER_VALUE) != _OWNER_MARKER:
        return {"status": "not_owned", "scope": "current_user", "extension": ".mcy",
                "progid": PROGID, "changed": []}
    changed = []
    # Never clear a default now owned by another program. The ProgID is our
    # fixed identifier; its other values are removed only when they carry our
    # exact marker or command/icon shape.
    values = [
        (EXTENSION_KEY, "", PROGID),
        (PROGID_KEY, "", "MeshCherry Project"),
        (PROGID_KEY, _OWNER_VALUE, _OWNER_MARKER),
    ]
    icon = _read_value(registry, DEFAULT_ICON_KEY)
    if isinstance(icon, str) and re.fullmatch(r'"[^"\r\n]+",0', icon):
        values.append((DEFAULT_ICON_KEY, "", icon))
    command = _read_value(registry, COMMAND_KEY)
    if isinstance(command, str) and _OWNED_COMMAND.fullmatch(command):
        values.append((COMMAND_KEY, "", command))
    for path, name, expected in values:
        if _delete_value(registry, path, name, expected):
            changed.append(path if not name else f"{path}\\{name}")
    # Remove empty descendants before their parents. Windows creates every
    # intermediate key, including DefaultIcon and the shell parent.
    for path in (COMMAND_KEY, OPEN_KEY, SHELL_KEY, DEFAULT_ICON_KEY,
                 PROGID_KEY, EXTENSION_KEY):
        if _delete_if_empty(registry, path):
            changed.append(path)
    if changed:
        (shell_notify or _notify_shell)()
        status = "uninstalled"
    else:
        status = "not_installed"
    return {"status": status, "scope": "current_user", "extension": ".mcy",
            "progid": PROGID, "changed": changed}
