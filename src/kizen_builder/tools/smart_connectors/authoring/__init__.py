"""The authoring path: create → set-input → generate-sample → push --publish
→ configure-flow → activate → start-flow. A sequence of stateful calls rather
than independent writes, hence the local ``plan_*`` / ``apply_*`` pairs in each
module here instead of the shared ``Plan`` framework.
"""

from __future__ import annotations
