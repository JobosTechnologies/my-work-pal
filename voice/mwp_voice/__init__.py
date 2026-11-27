"""My Work Pal voice layer (WP2 first stage).

A LiveKit voice front end for the Chief of Staff foundation: it speaks the
morning brief, walks the owner through the Review Queue, reads back before
any approval, and lets the foundation's own signed approval and guarded
execution path do the acting. This package never approves anything itself;
see foundation_bridge.ApprovalGateway for the one place a spoken decision
becomes a signed approval, and conversation.py for the part that can only
ask.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
