"""(Deprecated) Media source provider.

This integration implements media browsing via the `media_player` entity's
`async_browse_media()`.

Older versions contained a `media_source.py` provider that relied on APIs that
changed across Home Assistant releases and could cause integration load errors.

The file is intentionally kept as a no-op stub for backward compatibility with
deployments that may still reference it on disk.
"""

__all__: tuple[str, ...] = ()
