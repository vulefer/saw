# the release suite has no optional tests: a skip, for example a solver test
# that finds no cbc, is a gate that silently did not run, so it fails the run
import pytest


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = reporter.stats.get("skipped", []) if reporter is not None else []
    if skipped and exitstatus == pytest.ExitCode.OK:
        reporter.write_line(
            f"error: {len(skipped)} test(s) skipped, the release suite must run every test",
            red=True,
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
