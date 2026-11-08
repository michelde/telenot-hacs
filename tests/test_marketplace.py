"""Local packaging regressions; not a substitute for hassfest/HACS validation."""

import json
import tomllib
from pathlib import Path
from string import Formatter

import pytest

from tools.build_release import validate_release_tag

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "telenot"


def leaves(value, prefix=()):
    if isinstance(value, dict):
        return {path: text for key, child in value.items()
                for path, text in leaves(child, (*prefix, key)).items()}
    return {prefix: value}


def placeholders(value):
    return {field for _, field, _, _ in Formatter().parse(value) if field}


def test_finished_translation_files_are_complete_and_have_matching_placeholders():
    assert not (INTEGRATION / "strings.json").exists()
    en = json.loads((INTEGRATION / "translations" / "en.json").read_text())
    de = json.loads((INTEGRATION / "translations" / "de.json").read_text())
    assert "title" not in en  # The TELENOT brand is named in manifest.json.
    assert leaves(en).keys() == leaves(de).keys()
    for key, text in leaves(en).items():
        translated = leaves(de)[key]
        assert "[%key:" not in text and "[%key:" not in translated
        assert placeholders(text) == placeholders(translated)
    assert "command_uncertain" in en["exceptions"]


def test_single_installable_integration_and_matching_versions():
    directories = [p.name for p in (ROOT / "custom_components").iterdir()
                   if p.is_dir() and p.name != "__pycache__"]
    assert directories == ["telenot"]
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    hacs = json.loads((ROOT / "hacs.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert manifest["version"] == project["project"]["version"]
    assert manifest["domain"] == INTEGRATION.name
    assert manifest["categories"] == ["security_safety.alarm_systems"]
    assert manifest["config_flow"] is True
    assert manifest["requirements"] == []
    assert hacs["homeassistant"] == "2026.11.0"
    assert (INTEGRATION / "brand" / "icon.png").is_file()
    assert "persistent_directory" not in hacs  # Data is outside the installed folder.


def test_release_tag_must_match_manifest_version():
    validate_release_tag("0.1.0", None)
    validate_release_tag("0.1.0", "0.1.0")
    with pytest.raises(ValueError, match="does not match"):
        validate_release_tag("0.1.0", "0.2.0")
