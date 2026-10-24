from importlib import metadata

import pytest
from pytest_mock import MockerFixture

from vcsinstall.cli.application import main


@pytest.mark.usefixtures("isolated_settings")
class TestMain:
    def test_help(self, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]) -> None:
        mocker.patch("sys.argv", ["vcsinstall", "--help"])

        with pytest.raises(SystemExit) as caught:
            main()

        assert caught.value.code == 0
        captured = capsys.readouterr()
        assert not captured.err
        assert "Install files into your repository" in captured.out

    @pytest.mark.parametrize("protocol", ["human", "llm", "automation"])
    def test_version_and_skill(self, mocker: MockerFixture, capsys: pytest.CaptureFixture[str], protocol: str) -> None:
        mocker.patch("sys.argv", ["vcsinstall", "-p", protocol, "version"])

        with pytest.raises(SystemExit) as caught:
            main()

        assert caught.value.code == 0
        captured = capsys.readouterr()
        assert not captured.err
        assert metadata.version("vcsinstall") in captured.out

        mocker.patch("sys.argv", ["vcsinstall", "-p", protocol, "skill", "usage"])

        with pytest.raises(SystemExit) as caught:
            main()

        assert caught.value.code == 0
        captured = capsys.readouterr()
        assert not captured.err
        assert "# `vcsinstall` Usage" in captured.out

    def test_invalid_protocol(self, mocker: MockerFixture, capsys: pytest.CaptureFixture[str]) -> None:
        mocker.patch("sys.argv", ["vcsinstall", "-p", "invalid", "version"])

        with pytest.raises(SystemExit) as caught:
            main()

        assert caught.value.code != 0
        captured = capsys.readouterr()
        assert not captured.err
        assert "--VCSINSTALL-CELL " in captured.out
