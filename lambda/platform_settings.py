"""The one place the platform's functions read their settings from.

Every limit, default and time the platform uses is described once, in config/settings.json (see docs/SETTINGS.md and docs/SETTINGS_PLAN.md).
tools/gen_settings.py turns that registry into platform_settings_data.py, which this module reads. A function asks for a setting by its key:

    platform_settings.value("storage.maxItems")          the number (or text) in the unit the registry says
    platform_settings.mb("frontend.maxZipMB")            the same, in bytes
    platform_settings.kb("gateway.maxBodyKB")            the same, in bytes
    platform_settings.hours("sessions.studentHours")     the same, in seconds
    platform_settings.minutes("sessions.lockoutMinutes") the same, in seconds
    platform_settings.default / minimum / maximum(key)   the shipped default and the range
    platform_settings.defaults("knowledge.student")      {name: default} of every setting under a prefix
    platform_settings.bounds("knowledge.student")        {name: (min, max)} of those that have a range

A setting the AWS template passes in as an environment variable (the registry names it in "env") is read from there first, so an installed platform keeps
behaving exactly as before. Nothing is clamped yet: the ranges become enforced limits with the runtime settings step of the plan.

(The module is not called "settings" because several platform modules already have a function of that name.)
"""
import os

from platform_settings_data import SETTINGS


def _entry(key):
    try:
        return SETTINGS[key]
    except KeyError:
        raise KeyError(f"unknown setting {key!r}: add it to config/settings.json and run  python tools/gen_settings.py") from None


def _convert(entry, raw):
    if entry["type"] == "int":
        return int(float(raw))
    if entry["type"] == "float":
        return float(raw)
    return str(raw)


def value(key):
    entry = _entry(key)
    raw = os.environ.get(entry["env"]) if entry["env"] else None
    return _convert(entry, raw) if raw not in (None, "") else entry["default"]


def default(key):
    return _entry(key)["default"]


def minimum(key):
    return _entry(key)["min"]


def maximum(key):
    return _entry(key)["max"]


def mb(key):
    return int(value(key) * 1024 * 1024)


def kb(key):
    return int(value(key) * 1024)


def minutes(key):
    return int(value(key) * 60)


def hours(key):
    return int(value(key) * 3600)


def defaults(prefix):
    return {key[len(prefix) + 1:]: entry["default"] for key, entry in SETTINGS.items() if key.startswith(prefix + ".")}


def bounds(prefix):
    return {key[len(prefix) + 1:]: (entry["min"], entry["max"]) for key, entry in SETTINGS.items()
            if key.startswith(prefix + ".") and entry["min"] is not None and entry["max"] is not None}
