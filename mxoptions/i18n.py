"""Tiny translation layer: English source strings, optional Ukrainian catalogue."""

import locale
import os
import sys

_catalogue = {}
language = "en"


def _system_language():
    if sys.platform == "win32":
        import ctypes

        name = ctypes.create_unicode_buffer(85)  # "uk-UA"
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(name, len(name)):
            return name.value.split("-")[0].lower()
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        value = os.environ.get(var)
        if value:
            return value.split(":")[0].split("_")[0].split(".")[0].lower()
    try:
        return (locale.getlocale()[0] or "en").split("_")[0].lower()
    except ValueError:
        return "en"


def setup(preferred="auto"):
    global _catalogue, language
    language = _system_language() if preferred in (None, "", "auto") else preferred
    if language == "uk":
        from .i18n_uk import STRINGS

        _catalogue = STRINGS
    else:
        language = "en"
        _catalogue = {}


def _(text):
    return _catalogue.get(text, text)
