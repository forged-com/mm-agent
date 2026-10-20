from pathlib import Path

import pytest

from mm_agent.__main__ import load
from mm_agent.types import SourceKind

EXAMPLES = Path(__file__).parents[2] / "examples"


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("agent.postgres.yaml", SourceKind.POSTGRES),
        ("agent.files.yaml", SourceKind.FILES),
        ("agent.hummingbot.yaml", SourceKind.SQLITE),
    ],
)
def test_example_parses(name: str, kind: SourceKind) -> None:
    cfg, _ = load(EXAMPLES / name)
    assert cfg.source.kind is kind
    assert cfg.aliases.pairs and cfg.aliases.venues


def test_every_example_is_covered() -> None:
    assert sorted(p.name for p in EXAMPLES.glob("*.yaml")) == [
        "agent.files.yaml",
        "agent.hummingbot.yaml",
        "agent.postgres.yaml",
    ]
