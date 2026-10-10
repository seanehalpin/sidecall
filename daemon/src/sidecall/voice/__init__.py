"""Voice calls (SPEC §6.8).

- `call`: the per-connection call: turns, replies, interruptions and announcements, with
  seams for the repo session and the media backend. No Pipecat or MLX imports, so it is
  cheap to import and test with fakes.
- `turns`: turn assembly, pick binding and continuation rules (SPEC §6.9). Pure.
- `models`, `services`, `talker`, `pipeline`: the Pipecat pipeline. Importing
  `pipeline` applies the loopback-only ICE patch (`loopback`, SPEC §4).
"""
