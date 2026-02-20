import requests
import urllib
from typing import Any, Dict, List


class EspoAPIError(Exception):
    """An exception class for the client"""


def http_build_query(data: Any) -> str:
    parents = list()
    pairs = dict()

    def renderKey(parents: List[Any]) -> str:
        depth, outStr = 0, ""
        for x in parents:
            s = "[%s]" if depth > 0 or isinstance(x, int) else "%s"
            outStr += s % str(x)
            depth += 1
        return outStr

    def r_urlencode(data: Any) -> None:
        if isinstance(data, list) or isinstance(data, tuple):
            for i in range(len(data)):
                parents.append(i)
                r_urlencode(data[i])
                parents.pop()
        elif isinstance(data, dict):
            for key, value in data.items():
                parents.append(key)
                r_urlencode(value)
                parents.pop()
        else:
            pairs[renderKey(parents)] = str(data)

    r_urlencode(data)
    return urllib.parse.urlencode(pairs)


class EspoAPI:
    def __init__(self, url: str, api_key: str) -> None:
        self.url = url
        self.api_key = api_key
        self.status_code: int | None = None

    def request(
        self, method: str, action: str, params: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        if params is None:
            params = {}

        headers = {}

        headers["X-Api-Key"] = self.api_key

        url = self.normalize_url(action)

        if method in ["POST", "PATCH", "PUT"]:
            response = requests.request(method, url, headers=headers, json=params)
        else:
            if params:
                url = url + "?" + http_build_query(params)
            response = requests.request(method, url, headers=headers)

        self.status_code = response.status_code

        if self.status_code != 200:
            reason = self.parse_reason(response.headers)
            raise EspoAPIError(
                f"Wrong request, status code is {response.status_code}, reason is {reason}"
            )

        data = response.content
        if not data:
            raise EspoAPIError("Wrong request, content response is empty")

        json_data = response.json()
        if not isinstance(json_data, dict):
            raise EspoAPIError("API response is not a JSON object")
        return json_data

    def download_file(self, action: str, params: Dict[str, Any] | None = None) -> bytes:
        """Download a file from the API and return the binary content."""
        if params is None:
            params = {}

        headers = {"X-Api-Key": self.api_key}

        url = self.normalize_url(action)
        if params:
            url = url + "?" + http_build_query(params)

        response = requests.get(url, headers=headers)

        self.status_code = response.status_code

        if self.status_code != 200:
            reason = self.parse_reason(response.headers)
            raise EspoAPIError(
                f"Wrong request, status code is {response.status_code}, reason is {reason}"
            )

        return response.content

    def upload_file(
        self,
        file_content: bytes,
        filename: str,
        related_type: str = "Contact",
        related_id: str = "",
        field: str = "resume",
    ) -> Dict[str, Any]:
        """Upload a file to EspoCRM and return the attachment record."""
        import base64
        import mimetypes

        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(filename)
        if not mime_type:
            mime_type = "application/octet-stream"

        # Encode file content as base64 data URI
        file_base64 = base64.b64encode(file_content).decode("utf-8")
        data_uri = f"data:{mime_type};base64,{file_base64}"

        # Prepare JSON payload
        payload = {
            "name": filename,
            "type": mime_type,
            "role": "Attachment",
            "relatedType": related_type,
            "relatedId": related_id,
            "field": field,
            "file": data_uri,
        }

        response = self.request("POST", "Attachment", payload)
        return response

    def normalize_url(self, action: str) -> str:
        return self.url + "/" + action

    @staticmethod
    def parse_reason(headers: Any) -> str:
        if "X-Status-Reason" not in headers:
            return "Unknown Error"

        return str(headers["X-Status-Reason"])
