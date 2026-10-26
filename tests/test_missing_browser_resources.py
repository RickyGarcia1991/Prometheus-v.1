import pytest
from prometheus_assistant import browser_agent

def test_missing_optional_browser_resources_report_unavailable():
    status=browser_agent.status(None)
    assert status['provider']=='browser_use'
    assert status['installed'] is False
    assert status['available'] is False
    assert status['integrity']=='missing-or-unverified'

def test_missing_resources_fail_before_bundle_or_execution():
    with pytest.raises(ValueError,match='not configured'):
        browser_agent._verify_bundle(None)
