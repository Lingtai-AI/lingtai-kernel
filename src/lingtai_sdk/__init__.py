"""lingtai-sdk — batteries-included tool/capability implementations for LingTai agents.

This is the third importable package in the LingTai distribution, sitting between
the minimal ``lingtai_kernel`` (the agent runtime) and the ``lingtai`` wrapper
(the product/CLI surface). The SDK owns the *engineering substrate* behind the
capability seam — the tool implementations and service backends that should trend
away from product-wrapper coupling and never belong to the mono intrinsics.

Dependency direction is a transition target:

    lingtai_kernel  ←  lingtai_sdk  ←  lingtai

``lingtai_kernel`` must never import ``lingtai_sdk`` (mirroring the
kernel/wrapper rule). ``lingtai_sdk`` may import ``lingtai_kernel``. During the
first peel, the moved file capabilities still have two documented wrapper edges
(``lingtai.i18n`` strings and the packaged sidecar under ``lingtai/bin``); those
are compatibility seams to eliminate in later slices. The wrapper ``lingtai``
re-exports SDK symbols so existing imports keep working.

First slice (SDK-01 + SDK-02): the capability registry and the File-I/O tool
capabilities (read/write/edit/glob/grep) + FileIO service backends live here.
"""
