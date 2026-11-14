"""Executive assistant: one agent working across several mail and calendar providers.

Each provider (Microsoft 365, Google Workspace, Zoho, any IMAP/CalDAV host such as cPanel) sits behind the
same small interface in ``providers``. The agent in ``agent.py`` only ever sees canonical messages and events.
"""
