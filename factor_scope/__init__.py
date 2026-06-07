"""factor-scope — nightly decision-support for an A-share / funds-and-ETFs portfolio.

A local-first, single-user, nightly-batch decision-support engine. Each run emits one dated
artifact, ``dashboard.json`` (see :mod:`factor_scope.contract`), which a human reviews each
morning. The engine never places orders.
"""

__version__ = "0.1.0"
