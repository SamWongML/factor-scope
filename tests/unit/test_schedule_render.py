"""Unit tests for the scheduling adapter.

The adapter is a *thin renderer*: given a :class:`ScheduleSpec` it emits a macOS **launchd** plist
(the Mac-mini production path) or a **cron** line (the Linux alternative). No platform code on the
critical path — these are pure, deterministic string renders so the nightly job is reproducible
and reviewable before it is ever installed.
"""

import plistlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factor_scope.cli import app
from factor_scope.schedule import ScheduleSpec, render_cron_line, render_launchd_plist

pytestmark = pytest.mark.unit

runner = CliRunner()


def _spec(**over: object) -> ScheduleSpec:
    base = dict(
        label="com.factor-scope.nightly",
        program_arguments=("factor-scope", "nightly"),
        hour=22,
        minute=30,
        working_directory=Path("/srv/factor-scope"),
        stdout_path=Path("/var/log/fs.out"),
        stderr_path=Path("/var/log/fs.err"),
    )
    base.update(over)
    return ScheduleSpec(**base)  # type: ignore[arg-type]


def test_cron_line_fires_at_the_scheduled_minute_and_hour() -> None:
    line = render_cron_line(_spec())
    # Standard 5-field cron: minute hour day-of-month month day-of-week.
    assert line.startswith("30 22 * * *")


def test_cron_line_runs_the_command_in_the_working_directory_with_logs() -> None:
    line = render_cron_line(_spec())
    assert "cd /srv/factor-scope && factor-scope nightly" in line
    assert ">> /var/log/fs.out 2>> /var/log/fs.err" in line


def test_cron_line_prefixes_environment_assignments_before_the_command() -> None:
    # cron sources no shell rc files, so live source keys ride inline on the command itself.
    line = render_cron_line(_spec(environment={"FRED_API_KEY": "x"}))
    assert "&& FRED_API_KEY=x factor-scope nightly" in line
    # no environment given → no assignments, the line is unchanged.
    assert "FRED_API_KEY" not in render_cron_line(_spec())


def test_cron_line_shell_quotes_values_so_a_space_stays_one_token() -> None:
    # The EDGAR identity is "Name email" — it has a space. Unquoted, the shell would read the
    # second word as the command (not part of the value) and the nightly would never run.
    line = render_cron_line(_spec(environment={"EDGAR_IDENTITY": "Jane Doe jane@x.com"}))
    assert "EDGAR_IDENTITY='Jane Doe jane@x.com'" in line


def test_launchd_plist_is_valid_xml_that_schedules_the_one_shot_job() -> None:
    xml = render_launchd_plist(_spec(hour=22, minute=0))
    parsed = plistlib.loads(xml.encode("utf-8"))  # round-trips → it is a valid plist

    assert parsed["Label"] == "com.factor-scope.nightly"
    assert parsed["ProgramArguments"] == ["factor-scope", "nightly"]
    assert parsed["StartCalendarInterval"] == {"Hour": 22, "Minute": 0}
    assert parsed["WorkingDirectory"] == "/srv/factor-scope"
    # A nightly batch, not a service: it must not fire merely on load.
    assert parsed["RunAtLoad"] is False


def test_launchd_plist_includes_environment_only_when_given() -> None:
    assert "EnvironmentVariables" not in plistlib.loads(render_launchd_plist(_spec()).encode())
    with_env = render_launchd_plist(_spec(environment={"FRED_API_KEY": "x"}))
    assert plistlib.loads(with_env.encode())["EnvironmentVariables"] == {"FRED_API_KEY": "x"}


def test_renders_are_deterministic() -> None:
    assert render_launchd_plist(_spec()) == render_launchd_plist(_spec())
    assert render_cron_line(_spec()) == render_cron_line(_spec())


def test_schedule_command_emits_a_launchd_plist_by_default() -> None:
    result = runner.invoke(app, ["schedule", "--hour", "22", "--minute", "0"])
    assert result.exit_code == 0, result.output
    parsed = plistlib.loads(result.stdout.encode("utf-8"))
    assert parsed["StartCalendarInterval"] == {"Hour": 22, "Minute": 0}
    program = parsed["ProgramArguments"]
    # launchd runs with a minimal PATH, so the job must invoke factor-scope by absolute path.
    assert Path(program[0]).is_absolute() and Path(program[0]).name == "factor-scope"
    assert program[1] == "nightly"


def test_schedule_bakes_an_absolute_path_even_when_which_resolves_relative(monkeypatch) -> None:
    # shutil.which returns a *relative* path if PATH holds a relative entry (e.g. "."); launchd/cron
    # run from a different cwd with a minimal PATH, so the baked path must be absolute regardless.
    monkeypatch.setattr("factor_scope.cli.shutil.which", lambda _name: "rel/dir/factor-scope")
    result = runner.invoke(app, ["schedule"])
    assert result.exit_code == 0, result.output
    program = plistlib.loads(result.stdout.encode("utf-8"))["ProgramArguments"]
    assert Path(program[0]).is_absolute() and Path(program[0]).name == "factor-scope"


def test_schedule_command_injects_env_into_launchd_environment_variables() -> None:
    # Live source keys (e.g. FRED_API_KEY) reach the launchd job only via EnvironmentVariables —
    # it sources no shell rc files. Repeatable --env builds that dict.
    result = runner.invoke(
        app, ["schedule", "--env", "FRED_API_KEY=abc", "--env", "PATH=/opt/bin"]
    )
    assert result.exit_code == 0, result.output
    parsed = plistlib.loads(result.stdout.encode("utf-8"))
    assert parsed["EnvironmentVariables"] == {"FRED_API_KEY": "abc", "PATH": "/opt/bin"}


def test_schedule_command_can_emit_a_cron_line() -> None:
    result = runner.invoke(app, ["schedule", "--kind", "cron", "--hour", "3", "--minute", "15"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().startswith("15 3 * * *")
    assert "factor-scope nightly" in result.stdout


def test_schedule_command_injects_env_into_cron_line() -> None:
    result = runner.invoke(app, ["schedule", "--kind", "cron", "--env", "FRED_API_KEY=abc"])
    assert result.exit_code == 0, result.output
    # the key rides inline before the command, which is the absolute factor-scope path.
    assert "FRED_API_KEY=abc " in result.stdout
    assert "/factor-scope nightly" in result.stdout


def test_schedule_command_writes_to_a_file_when_asked(tmp_path) -> None:
    out = tmp_path / "com.factor-scope.nightly.plist"
    result = runner.invoke(app, ["schedule", "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert plistlib.loads(out.read_bytes())["Label"] == "com.factor-scope.nightly"


def test_schedule_can_target_the_discover_job() -> None:
    # The discovery service is its own (weekly) job — the same renderer, a different program.
    result = runner.invoke(app, ["schedule", "--job", "discover"])
    assert result.exit_code == 0, result.output
    parsed = plistlib.loads(result.stdout.encode("utf-8"))
    program = parsed["ProgramArguments"]
    assert Path(program[0]).is_absolute() and Path(program[0]).name == "factor-scope"
    assert program[1] == "discover"


def test_schedule_rejects_an_unknown_job() -> None:
    result = runner.invoke(app, ["schedule", "--job", "weekly"])
    assert result.exit_code != 0


def test_schedule_rejects_malformed_env() -> None:
    # A bare token with no '=' is never a silent no-op — it fails loudly.
    result = runner.invoke(app, ["schedule", "--env", "NOEQUALS"])
    assert result.exit_code != 0


def test_schedule_rejects_an_empty_env_value() -> None:
    # `--env KEY=` would silently bake an empty credential (e.g. an unset `"$FRED_API_KEY"`
    # expanding to nothing), masking a missing key. An empty value fails loudly instead.
    result = runner.invoke(app, ["schedule", "--env", "FRED_API_KEY="])
    assert result.exit_code != 0
