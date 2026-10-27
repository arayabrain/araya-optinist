"""The Olympus SDK loads in this image. Its library needs an executable stack,
which glibc 2.41 and later refuse with an OSError, so this fails rather than
skips when the backend base image moves to such a glibc."""
import platform

import pytest

from studio.app.optinist.microscopes.OIRReader import OIRReader


@pytest.mark.skipif(
    (platform.system(), platform.machine()) != ("Linux", "x86_64"),
    reason="the SDK ships a Linux x86_64 library only",
)
def test_the_olympus_sdk_loads():
    OIRReader()
