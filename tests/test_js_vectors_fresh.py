"""
Guards the Python <-> JavaScript link. site/test_vectors.json holds
expected values computed by the Python reference; site/test_engine.js
replays them against site/engine.js. If the Python math changes and the
vectors aren't regenerated, the JS check would pass against stale numbers
-- this test fails instead.

Fix: python tests/gen_js_vectors.py   then   node site/test_engine.js
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import gen_js_vectors  # noqa: E402

VECTORS = os.path.join(os.path.dirname(__file__), "..", "site", "test_vectors.json")


def test_checked_in_vectors_match_the_python_reference():
    with open(VECTORS) as fh:
        on_disk = json.load(fh)
    fresh = json.loads(json.dumps(gen_js_vectors.build()))
    assert on_disk == fresh, (
        "site/test_vectors.json is stale. Run: python tests/gen_js_vectors.py "
        "&& node site/test_engine.js"
    )


def test_vectors_cover_every_numeric_group():
    with open(VECTORS) as fh:
        data = json.load(fh)
    for group in ("erf", "norm_cdf", "norm_ppf", "single_line", "ladder",
                  "calculate_price", "odds_with_margin", "fair_american", "devig"):
        assert len(data[group]) >= 40, group
