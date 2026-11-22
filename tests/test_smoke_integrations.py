"""scripts/smoke_integrations.py: offline checks of its reporting (no network: the checks are stubbed)."""
from app.config import Settings
from scripts import smoke_integrations as smoke


def _settings(**kw) -> Settings:
    base = dict(github_token=None, notes_dir=None, calendar_ics_url=None, github_user=None)
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_every_integration_is_skipped_when_nothing_is_configured():
    rows = smoke.run(_settings())
    assert [r[0] for r in rows] == ["GitHub", "Notes folder", "Calendar", "Email"]
    assert all(status == "SKIPPED(no key)" for _, status, _ in rows)


def test_a_failure_never_prints_the_key_and_other_checks_still_run():
    secret = "ghp_SECRET_github_token_value"

    def leaky(s):
        raise RuntimeError(f"GET https://api.github.com/user?token={s.github_token} failed")

    rows = smoke.run(_settings(github_token=secret), [("GitHub", leaky), ("Notes folder", smoke.check_notes)])
    assert rows[0][1] == "FAIL" and secret not in rows[0][2] and "***" in rows[0][2]
    assert rows[1][1] == "SKIPPED(no key)"


def test_notes_check_is_read_only_and_counts_files(tmp_path):
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("y")
    rows = smoke.run(_settings(notes_dir=str(tmp_path)), [("Notes folder", smoke.check_notes)])
    assert rows == [("Notes folder", "PASS", "2 file(s) readable (not indexed)")]
    assert smoke.run(_settings(notes_dir=str(tmp_path / "missing")), [("Notes", smoke.check_notes)])[0][1] == "FAIL"
