import pytest

from stockscan import cli


def test_help(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    assert "serve" in capsys.readouterr().out


def test_addresses():
    for url in cli.addresses(3000):
        assert url.startswith("http://") and url.endswith(":3000/")


def test_rerun_empty(tmp_path, capsys):
    assert cli.main(["rerun", "--data", str(tmp_path / "d")]) == 0
    assert "# 0 reports" in capsys.readouterr().out


def test_dictionary_commands(tmp_path, capsys):
    out = tmp_path / "d.txt"
    assert cli.main(["dictionary", "generate", "--n", "6", "--d", "9", "--count", "50", "--out", str(out)]) == 0
    assert cli.main(["dictionary", "check", "--file", str(out), "--r", "3", "--mc", "2000"]) == 0
    text = capsys.readouterr().out
    assert "never identified as another marker" in text and "SS 0.1.0 DICT" in text and "OUTSIDE" not in text
