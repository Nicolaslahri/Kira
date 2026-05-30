"""Outbound integrations with downstream tools in the user's media stack.

Distinct from `providers/` (which fetches METADATA from external services
like TMDB/TVDB/AniDB into Kira). Integrations let Kira PUSH actions to
user-owned tools — currently Sonarr for missing-episode searches. Pattern
will repeat for Radarr (movies), Plex/Jellyfin (library refresh), Apprise
(notifications), etc.
"""
