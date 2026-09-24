"""Gateway package: REST API server for Main App integration and manual topic registration."""

from research.gateway.server import (
    GatewayService,
    create_gateway_server,
    make_gateway_handler,
)

__all__ = [
    "GatewayService",
    "create_gateway_server",
    "make_gateway_handler",
]
