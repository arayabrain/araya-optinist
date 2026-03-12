"""
Python version compatibility utilities.

This module provides backports and compatibility shims for features
that may not be available in all supported Python versions.
"""

# StrEnum was added in Python 3.11, provide fallback for older versions
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        """String enum compatible with Python < 3.11.

        Inheriting from (str, Enum) makes members behave as strings
        while retaining enum functionality.
        """


__all__ = ["StrEnum"]
