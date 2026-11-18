import agent


def test_agent_package_imports() -> None:
    assert agent.__version__ == "0.1.0"
