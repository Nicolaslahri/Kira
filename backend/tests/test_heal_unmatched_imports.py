"""Regression: heal-unmatched crashed with NameError — `select`/`MediaFile`
were never imported in the endpoint's scope, so the whole Sonarr auto-heal
chain 500'd whenever Sonarr was configured (steps 1-3 return early otherwise,
which is why it looked fine unconfigured)."""
from __future__ import annotations

import ast
import inspect
import textwrap


def test_heal_endpoint_has_its_names():
    from kira.api import integrations

    fn = None
    for name in ("heal_unmatched", "sonarr_heal_unmatched", "heal_from_sonarr"):
        fn = getattr(integrations, name, None)
        if fn is not None:
            break
    assert fn is not None, "heal endpoint function not found"

    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update((a.asname or a.name).split(".")[0] for a in node.names)

    # Names used by the candidate query — module scope doesn't provide them.
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    module_scope = set(dir(integrations))
    for needed in ("select", "MediaFile"):
        assert needed not in used or needed in imported or needed in module_scope, (
            f"{needed!r} is used but neither imported in the function nor module scope"
        )
    # And the concrete regression: both must be resolvable.
    assert "select" in imported or "select" in module_scope
    assert "MediaFile" in imported or "MediaFile" in module_scope
