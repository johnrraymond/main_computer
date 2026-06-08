"""Removed Gmail OAuth/Gmail API integration.

Main Computer email is intentionally IMAP/POP3/SMTP only. This module remains
as an inert compatibility tombstone for raw snapshot patching, because raw
new_patch.py snapshot zips do not represent file deletion by omission.
"""

__all__: list[str] = []
