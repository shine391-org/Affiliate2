"""ViralMint backend overlay for the Content Engine operator console.

When copied into tools/ViralMint, importing this package registers the review
router through ViralMint's documented overlay seam. Importing it from root
unit tests is harmless when ViralMint is not on sys.path.
"""
try:
    from backend.core import plugins
    from .api.review import router

    plugins.register_router(router)
except ImportError:
    # Root-level tests exercise review_store without requiring ViralMint.
    pass
