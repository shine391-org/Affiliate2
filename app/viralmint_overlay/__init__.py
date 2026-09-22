"""ViralMint backend overlay for the Content Engine operator console.

When copied into tools/ViralMint, importing this package registers the review
router through ViralMint's documented overlay seam. Importing it from root
unit tests is harmless when ViralMint itself is not on sys.path.
"""
try:
    from backend.core import plugins
except ImportError:
    # Root-level tests do not need the ViralMint runtime.
    plugins = None

if plugins is not None:
    # Import failures inside our own router should surface instead of being
    # mistaken for "ViralMint is not installed".
    from .api.review import router as review_router
    from .api.publish import router as publish_router
    from .api.performance import router as performance_router

    plugins.register_router(review_router)
    plugins.register_router(publish_router)
    plugins.register_router(performance_router)
