"""The UI gate is required, real-stack, exact-case and exports sanitized evidence."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_history_browser_is_required_after_build_and_chromium_before_main_stack():
    workflow = (ROOT / ".github/workflows/v1-validation.yml").read_text()
    marker = "      - name: Require real isolated RD history browser acceptance\n"
    assert workflow.count(marker) == 1
    before, remaining = workflow.split(marker)
    step, after = remaining.split("      - name:", 1)
    assert "if:" not in step and "continue-on-error:" not in step
    assert "npm run build --prefix frontend" in before
    assert "playwright install --with-deps chromium" in before
    assert after.startswith(
        " Migrate and explicitly provision disposable synthetic accounts"
    )
    command = step
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in command
    assert "-u PYTEST_ADDOPTS -u PYTEST_PLUGINS" in command
    assert "::test_real_isolated_schema_history_browser" in command
    assert "assert len(cases) == 1" in command
    assert '("failure", "error", "skipped")' in command
    assert "ENGINE_HISTORY_BROWSER_EXIT" in command
    assert (
        'summarize(path, Path("var/ci-evidence/engine-history-browser-junit.xml"))'
        in command
    )
    assert '> "$RUNNER_TEMP/engine-history-browser-private.log" 2>&1' in command
    assert "cat " not in command
