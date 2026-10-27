"""``integration_check.py --scenario envelopes`` run against the fake Live: every command and
argument the probe sends must reach the bridge (real Live then answers what matters — which
routes keep clip envelopes in the arrangement)."""

import sys
from pathlib import Path

import Live
from live_stub_ext import arrangement_live

sys.path.insert(0, str(Path(__file__).resolve().parent))

import integration_check  # noqa: E402


def test_envelope_probe_runs_every_case_and_cleans_up(tcp_bridge, song, capsys):
    uninstall = arrangement_live.install(Live, copy_envelopes="without_devices")
    try:
        before = [t.name for t in song.tracks]
        client = integration_check.Client("127.0.0.1", tcp_bridge.port, "", 10.0)
        client.connect()
        report = integration_check.Report()
        checker = integration_check.Checker(client, report)
        checker.catalogue = {}                          # every command counts as offered
        checker.ENV_RECORD_TIMEOUT = 0.5                # the fake playhead does not move
        try:
            checker.run_envelopes_scenario()
        finally:
            client.close()
    finally:
        uninstall()
    rows = {row["step"]: row for row in report.rows}
    # nothing may fail or be skipped for a wrong argument
    problems = [r for r in report.rows if r["status"] == "FAIL" or
                (r["status"] == "SKIP" and "bad_args" in r["detail"].lower())]
    assert problems == [], problems
    findings = {key: kept for key, _label, kept, _note in checker.env_findings}
    # the fake models Live 12.4.5 (§19.5): kept without devices, lost with an instrument
    assert findings["A"] == {"Track Volume": True}
    assert set(findings["B"].values()) == {False} and len(findings["B"]) == 2
    assert "E" in findings and "F" in findings, rows
    assert [t.name for t in song.tracks] == before      # every LB_env track deleted
    out = capsys.readouterr().out
    assert "Clip envelopes in the arrangement" in out and "A  no devices" in out
