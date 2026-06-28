"""Minimal no-dependency PEP 517 backend.

The project is intended to run directly with ``PYTHONPATH=src``.  This backend
exists only so the dependency-free pyproject is valid; packaging is deliberately
outside the proof-of-concept scope.
"""


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    raise RuntimeError("Run this proof of concept with PYTHONPATH=src; wheel building is not supported.")


def build_sdist(sdist_directory, config_settings=None):
    raise RuntimeError("Source distribution building is not supported for this proof of concept.")
