"""Pinned local screens; no artwork is stored or changed by this registry.

These records contain no art and do not copy, download or modify screen files.
Issue #15 records R10 design approval. Pi LCD/live verification remains separate.
"""
from types import MappingProxyType


SCREEN_PROFILES = MappingProxyType({
    "v13.2": MappingProxyType({
        "default_path": "/home/bgh/Downloads/BMO_Screen_v13_2.html",
        "sha256": "38250760bb49ba90297ece20ceb669e2e3421a073a6590cdc184c0971e5ad932",
        "label": "Approved original v13.2",
        "approval_status": "approved",
        "pi_live_verified": False,
    }),
    "r10": MappingProxyType({
        "default_path": "/home/bgh/Downloads/BMO_Screen_v13_2_7inch_preview_v10.html",
        "sha256": "8c3ef98ba0e0645213c95295a24d7353fc2865b5ad9bc85390360c4ec9b78c82",
        "label": "Approved R10 design (Pi live NOT_RUN)",
        "approval_status": "approved_design",
        "pi_live_verified": False,
    }),
})
