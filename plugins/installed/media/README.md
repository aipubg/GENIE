# media plugin

Native media control for GENIE.

* **Control** uses the Windows media-key interface (`SendInput` + `VK_MEDIA_*`), which the OS
  routes to the active media session (Spotify, browser tab, VLC, …). No screen automation.
* **Observation** reads visible media windows; if nothing is playing it reports UNAVAILABLE
  rather than guessing.
* Play/pause share one OS key — the OS decides based on the current state, so GENIE always
  observes the result afterwards instead of assuming it.

Permissions: `application.media.control`, `system.audio.read`.
