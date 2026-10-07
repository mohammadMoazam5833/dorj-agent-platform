#!/usr/bin/env python3
"""No-op compatibility hook for Dorj Agent 1.6.6+ WebSocket close codes.

Upstream now sends RFC 6455 close payloads and reports the received close as
``onClose(info)`` on the outbound client. The gateway only needs to send
``sendClose(4001, "session_replaced")``; the customer client already parses the
payload. Keep this entry point so older build scripts remain compatible.
"""
