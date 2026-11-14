"""External helpdesk adapter.

The local incident ticket table is the working implementation.
This adapter stays inactive until an operator stores credentials
in integration_settings.
"""

from typing import Protocol


class TicketResult(dict):
    pass


class HelpdeskAdapter(Protocol):
    async def create_ticket(self, title: str, body: str) -> dict: ...


class NotConfiguredHelpdesk:
    name = "external_helpdesk"

    async def create_ticket(self, title: str, body: str) -> dict:
        return {"status": "not_configured", "provider": self.name, "title": title}
