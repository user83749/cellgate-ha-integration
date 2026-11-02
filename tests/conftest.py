"""Load the actual Home Assistant custom-integration test fixtures."""

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def custom_integration_support(enable_custom_integrations):
    """Allow loading custom_components/cellgate through Home Assistant."""
