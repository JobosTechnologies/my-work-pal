"""The plugin's secrets, and the one place that reads them.

Standalone operation: secrets come from the command environment, plus the
optional plugin-root ``.env`` that ``config_loader`` loads at startup. Every
secret the code can read is declared in ``plugin.yaml`` -> ``config_schema``
with its ``env`` name, so there is one list to audit.
"""

from __future__ import annotations

import os
from typing import Mapping

# env name -> description (declared in plugin.yaml config_schema).
SECRETS: Mapping[str, str] = {
    "M365_CLIENT_SECRET": "Microsoft Entra app client secret, for the m365 workspace provider (client-credentials flow).",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "Google service-account key JSON (the file's contents), for the google_api provider's REST writes.",
    "DOCUSEAL_API_KEY": "DocuSeal API key, for the esign-connector skill (self-hosted DocuSeal).",
    "DOCUSEAL_MCP_TOKEN": "DocuSeal MCP token, if DocuSeal is reached through its MCP server.",
    "CHIEF_OF_STAFF_WEBHOOK_SECRET": "Shared secret that signs inbound workspace webhooks (optional webhook mode).",
    "CHIEF_OF_STAFF_WEBHOOK_CHANNEL_TOKEN": "Google push-channel token checked on inbound webhooks (optional webhook mode).",
}

SA_JSON_ENV = "GOOGLE_SERVICE_ACCOUNT_JSON"
# Stands in for a key-file path when the service-account key comes from the secret.
SA_SECRET_SOURCE = f"secret:{SA_JSON_ENV}"


def get(name: str, default: str | None = None) -> str | None:
    """Read a credential from the command environment (incl. loaded .env)."""
    value = os.getenv(name, default)
    if value is None:
        return default
    value = str(value).strip()
    return value or default
