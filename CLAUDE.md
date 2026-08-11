# flm-tray

A PyQt6 system tray that controls a FastFlowLM server running as a systemd user unit.

## Commands

```bash
python3 -m pytest tests/ -q          # 155 cases, no Qt, no network, under a second
python3 -m py_compile flm_tray.py    # the Qt shell has no test coverage
/usr/bin/python3 flm_tray.py &       # never conda or venv; PyQt6 is a system package
./install.sh                         # host terminal only, a sandbox cannot reach host systemd
```

Debugging the server:

```bash
systemctl --user status flm.service
journalctl --user -u flm.service -n 200 -f
flm validate                         # memlock must report infinity
```

## Pattern: journal markers drive the icon

The tray follows `journalctl -o cat` and matches `PHASE_MARKERS` in `flm_state.py`. Match on the line prefix, never a bare substring, because flm logs the model's generated text into the same stream:

```python
("[FLM]", "Loading model:", "loading")   # the server's own line, matches
# "Here is a summary: Loading model: is what it does" from a model answer must not
```

To add a state: add the marker tuple, add a case to `visual_state()`, add a test.

## Pattern: logic belongs in flm_state.py

`flm_state.py` imports no Qt, which is what lets the suite run headless. `flm_tray.py` is the shell and is untested. Anything decided inside a Qt callback cannot be tested, so write it as a pure function there and call it from the callback.

`tests/test_flm_state.py` was written from a written contract by someone who could not see the code, so it encodes the spec rather than the implementation. When behaviour changes, restate the rule in the test instead of reshaping it around what the code now does.

## Pattern: switching models

The tray writes `FLM_MODEL=<name>` to `~/.config/flm-tray/env` and restarts the unit, which reads it via `EnvironmentFile=`. Never put a model name in `ExecStart`.

`FLM_MODEL` sets only what is preloaded. FastFlowLM hot-swaps per request, so a client asking for a different model gets that one loaded instead. The resident model comes from `[FLM] Loading model:` paths through `resolve_model_tag()`, a fuzzy match because `Qwen3.6-35B-A3B-NPU2` is served as `qwen3.6-moe:35b-a3b`.

Tooltip, submenu title and radio mark must all name the same model. Route every one of them through `shown_model()`; reading the env file directly is what let the menu drift from the tooltip after a hot-swap.

## Git

Commit on `main`, never a feature branch or PR. Still ask before every commit.

## Constraints

- Never make the tray the parent of `flm serve`. The unit grants `LimitMEMLOCK=infinity`; a Plasma-session child inherits the PAM limit and fails at model load.
- The unit has no `[Install]` section on purpose, so nothing starts at login.
- The installed unit is a copy, so re-run `install.sh` after editing `flm.service`.
- No absolute paths in the repo. `install.sh` derives them.
- Serve on localhost only. No `--host 0.0.0.0`, because it is an unauthenticated LLM. Keep `--cors 0`.
- Run `flm list` with `FLM_DISABLE_UPDATE_CHECK=1`, or listing re-pulls every outdated model.
- Never estimate a tok/s or TTFT figure. The README table holds real `flm bench` output only.
- README follows the humanizer rules: no em or en dashes, sentence-case headings, no decorative emoji.
