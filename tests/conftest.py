import pytest

from stockscan import synth
from stockscan.store import DataDir


@pytest.fixture(scope="session")
def populated(tmp_path_factory):
    """A data directory with targets and synthetic photo sets of every kind."""
    dd = DataDir(tmp_path_factory.mktemp("data"))
    sets = synth.populate(dd, n_calib=10, n_front=2)
    return dd, sets
