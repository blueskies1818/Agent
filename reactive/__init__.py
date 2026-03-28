"""
reactive/ — Incoming communication sources.

This package normalizes messages from external inputs (webhooks, sockets,
file watchers, etc.) into a standard format for the engine to process.

Sources are added here as the system grows. Each source should ultimately
call engine.AgentLoop.run(message) to hand off to the agent.
"""