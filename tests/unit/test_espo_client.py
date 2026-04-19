"""Unit tests for the shared Espo client."""

from unittest.mock import Mock, patch

from five08.clients.espo import EspoClient
from five08.tls import default_ca_bundle_path


def test_list_contacts_uses_explicit_ca_bundle() -> None:
    """Espo requests should pin the repo's certifi bundle explicitly."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b'{"list": [], "total": 0}'
    mock_response.json.return_value = {"list": [], "total": 0}

    with patch(
        "five08.clients.espo.requests.request",
        return_value=mock_response,
    ) as mock_request:
        result = EspoClient("https://crm.example.com", "secret").list_contacts(
            {"offset": 0, "maxSize": 50}
        )

    assert result == {"list": [], "total": 0}
    mock_request.assert_called_once_with(
        "GET",
        "https://crm.example.com/api/v1/Contact?offset=0&maxSize=50",
        headers={"X-Api-Key": "secret"},
        timeout=20.0,
        verify=default_ca_bundle_path(),
    )
