"""GENIE channels — channel ingress + session continuity (P5, OpenClaw gap).

OpenClaw's valuable idea is that an assistant meets you in many *channels*
(Discord, Slack, Telegram, the desktop UI, voice) while the conversation stays
continuous across them. GENIE had none of that: the UI minted its own
`session_id` in the browser and the backend only threaded the string through,
so nothing survived a restart and no second channel could be added without
special-casing.

This package adds the missing pieces without importing OpenClaw (MIT, but a
Node.js app — integration is by contract, not by code):

    channels/sessions.py   persistent, resumable sessions + message history
    channels/service.py    ChannelAdapter contract + ChannelService ingress

Design rules:
  * a channel never becomes an authority — it is an ingress/egress transport;
  * inbound text always enters the SAME GENIE request path as the desktop UI
    and voice (one brain);
  * session history is persisted, so continuity survives restarts;
  * an unknown channel is rejected rather than silently treated as trusted.
"""
