"""Every translation mirrors strings.json: same keys, same placeholders."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parents[1] / "custom_components" / "singapore"
STRINGS = json.loads((COMPONENT / "strings.json").read_text())
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _leaves(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, f"{path}/{key}")
    else:
        yield path, node


def test_english_mirrors_strings_json():
    assert json.loads((COMPONENT / "translations" / "en.json").read_text()) == STRINGS


@pytest.mark.parametrize(
    "path", sorted((COMPONENT / "translations").glob("*.json")), ids=lambda p: p.name
)
def test_translation_matches_strings_json(path: Path):
    source = dict(_leaves(STRINGS))
    translated = dict(_leaves(json.loads(path.read_text())))
    assert translated.keys() == source.keys()
    for key, text in source.items():
        assert set(_PLACEHOLDER.findall(translated[key])) == set(
            _PLACEHOLDER.findall(text)
        ), key
