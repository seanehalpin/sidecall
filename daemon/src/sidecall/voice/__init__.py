"""Voice calls (SPEC §6.8).

- `call`: the per-connection call state machine and the seams (talker, backend). Pure:
  no Pipecat or MLX imports, so it is cheap to import and test with fakes.
- `models`, `services`, `talker`, `pipeline`: the Pipecat pipeline. Importing
  `pipeline` applies the loopback-only ICE patch (`loopback`, SPEC §4).
"""
