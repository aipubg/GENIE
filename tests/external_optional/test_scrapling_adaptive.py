"""Real upstream Scrapling ADAPTIVE relocation proof (external_optional).

This proves the ACTUAL upstream mechanism, not a GENIE imitation:

    Adaptor(html, url=URL, adaptive=True, storage_args={"storage_file": ...})
      -> Selector.css(selector, identifier=<id>, adaptive=True, auto_save=True)
           stores the logical target in upstream SQLiteStorageSystem
    then, after the DOM changes and the original selector is dead,
      -> the same identifier relocates the same logical target by similarity.

Upstream API used:
    scrapling.parser.Adaptor(..., adaptive=, storage=, storage_args=)
    scrapling.parser.Selector.css(selector, identifier=, adaptive=, auto_save=, percentage=)
    scrapling.parser.SQLiteStorageSystem  (storage implementation)
    similarity/relocation: upstream compares stored element attributes/text with a
    percentage threshold (default 40) and logs the top score when below it.

Scoping: storage is keyed by (storage_file, url, identifier). GENIE scopes it by giving
each mission/workspace/domain its own storage_file, so one mission never relocates
another's targets.
"""
from __future__ import annotations

import os

import pytest

scrapling = pytest.importorskip("scrapling", reason="scrapling is not installed here")

from scrapling.parser import Adaptor  # noqa: E402

URL = "https://shop.example/product/42"

HTML_A = """<html><body>
<div class="product">
  <h1>Widget Deluxe</h1>
  <div class="price-box">
    <span class="price">$19.99</span>
  </div>
</div>
</body></html>"""

# Realistic redesign: tag and text survive, but the class changes and the wrapper gains a
# version suffix, so the learned selector `.price` no longer matches.
HTML_B = """<html><body>
<div class="product">
  <h1>Widget Deluxe</h1>
  <div class="price-box v2">
    <span class="price-new" data-v="2">$19.99</span>
  </div>
</div>
</body></html>"""


def _adaptor(html: str, storage_file: str) -> Adaptor:
    """Upstream Adaptor with adaptive matching bound to one storage file."""
    return Adaptor(html, url=URL, adaptive=True,
                   storage_args={"storage_file": storage_file})


def _learn(storage_file: str):
    page = _adaptor(HTML_A, storage_file)
    return page.css(".price", identifier="product-price", adaptive=True, auto_save=True)


class TestAdaptiveRelocation:
    """The three facts that make adaptive relocation proven rather than assumed."""

    def test_learns_and_stores_target(self, tmp_path):
        """HTML A: the logical target is found and persisted by upstream storage."""
        storage_file = str(tmp_path / "adaptive_storage.db")
        found = _learn(storage_file)
        assert len(found) == 1
        assert found[0].text.strip() == "$19.99"
        # upstream really wrote its SQLite store
        assert os.path.isfile(storage_file)

    def test_original_selector_is_dead_after_change(self, tmp_path):
        """HTML B: without adaptive, the old selector genuinely no longer matches."""
        storage_file = str(tmp_path / "adaptive_storage.db")
        _learn(storage_file)
        page_b = _adaptor(HTML_B, storage_file)
        assert len(page_b.css(".price", adaptive=False)) == 0

    def test_adaptive_relocates_logical_target(self, tmp_path):
        """HTML B: upstream adaptive finds the same logical target by similarity."""
        storage_file = str(tmp_path / "adaptive_storage.db")
        _learn(storage_file)
        page_b = _adaptor(HTML_B, storage_file)

        relocated = page_b.css(".price", identifier="product-price",
                               adaptive=True, percentage=40)

        assert len(relocated) == 1, "upstream adaptive failed to relocate the target"
        assert relocated[0].text.strip() == "$19.99"
        assert relocated[0].tag == "span"

    def test_storage_is_scoped_per_mission(self, tmp_path):
        """A separate storage file is a separate world: no cross-mission relocation."""
        mission_one = str(tmp_path / "mission_one.db")
        mission_two = str(tmp_path / "mission_two.db")

        _learn(mission_one)                      # only mission one learned the target

        page_b = _adaptor(HTML_B, mission_two)   # mission two never learned it
        found = page_b.css(".price", identifier="product-price", adaptive=True)

        assert len(found) == 0, "adaptive data leaked across mission storage scopes"
