"""Initial sanity tests for hexaflow.

Notes/Architectural Intent:
    Validates package import and metadata attributes.
"""

import hexaflow


def test_version_defined() -> None:
    """Validate version string is present."""
    version = hexaflow.__version__
    assert version == "0.0.0"
