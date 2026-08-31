"""Executable checks for the declared v0.2.0 compatibility contract."""

import platform
import sys
import sysconfig

from saw.backends.pulp_backend import PulpCbcAdapter


SUPPORTED_CPYTHON_MIN = (3, 10)
SUPPORTED_CPYTHON_MAX_EXCLUSIVE = (3, 15)
SUPPORTED_PLATFORMS = {
    "Linux": {"x86_64", "amd64"},
    "Windows": {"x86_64", "amd64"},
    "Darwin": {"x86_64", "amd64"},
}


def test_release_environment_is_in_the_supported_matrix() -> None:
    version = sys.version_info[:2]
    system = platform.system()
    machine = platform.machine().lower()

    assert sys.implementation.name == "cpython"
    assert SUPPORTED_CPYTHON_MIN <= version < SUPPORTED_CPYTHON_MAX_EXCLUSIVE
    assert sysconfig.get_config_var("Py_GIL_DISABLED") != 1
    assert system in SUPPORTED_PLATFORMS
    assert machine in SUPPORTED_PLATFORMS[system]


def test_packaged_cbc_is_available() -> None:
    availability = PulpCbcAdapter().check_availability()

    assert availability.available, availability.reason
