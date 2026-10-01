# Marketplace submission checklist

Not submitted yet. Submit after the GitHub repo is public and PromptBro has had real use.
Follow `SUBMISSION.md` in the `omacom/omarchy-plugin-marketplace` repository, and apply anything
reviewers flagged on other plugins first.

## Before submitting

- [ ] Repo `supercleanse/promptbro` is public, with `manifest.json` at the root and a tagged
      release matching `version` (currently 0.2.0).
- [ ] `omarchy plugin validate .` exits 0 and `tests/run.sh` is green.
- [ ] README install line works on a clean account:
      `omarchy plugin add https://github.com/supercleanse/promptbro --enable`, then Set up.
- [ ] `preview.png` is current (`scripts/make_preview.py`).
- [ ] The public history was reviewed for personal paths, names and data (see below).
- [ ] MIT `LICENSE` present; the plugin id `supercleanse.promptbro` is final (permanent once listed).

## Issue draft

- Title: `[Plugin]: PromptBro`
- Category: Productivity
- Tags: `ai`, `bar`, `media`; suggested new tag: `teleprompter`
- Repository: https://github.com/supercleanse/promptbro
- Plugin id: `supercleanse.promptbro`; kind: bar widget

## Notes for the reviewers

Expect `installer` and `package-manager` capabilities and a `review-required` outcome. State
plainly:

- The virtual environment at `~/.local/share/promptbro/venv` and the `pip install` of the pinned
  packages in `requirements.txt` run only when the user runs `promptbro setup` (from the menu or a
  terminal). Nothing installs on `omarchy plugin add`.
- The speech model downloads from the sherpa-onnx GitHub releases, over HTTPS, with a pinned
  SHA-256 checked before extraction. Archive members with absolute paths or `..` are rejected.
- No `sudo`, ever. If system packages are missing, setup prints the `pacman` line and stops.
- No services, timers or background processes. The prompter runs only while the user has it open.
- Nothing is written outside PromptBro's own directories (`~/.config/promptbro`,
  `~/.local/share/promptbro`, `~/.local/state/promptbro`, `$XDG_RUNTIME_DIR/promptbro`), apart
  from the optional launcher entry, icon and `~/.local/bin/promptbro` link (skip with
  `--no-desktop`).
- Hyprland hotkeys and layer rules are printed for the user to paste. PromptBro never edits
  Hyprland files.
- The status file is created with mode 0600 in a 0700 runtime directory, written atomically.
- No network access after the model download; recognition is entirely on-device.
- `promptbro uninstall-data` removes everything setup created.

## Publishing the repo: history

The development history contains the author's home paths, internal project names and debugging
notes. Shipped files are clean (checked by `tests/test_manifest.py`), and `docs/` has been
redacted, but earlier commits are not. Publish a fresh history rather than pushing this one:

```bash
git checkout --orphan public && git add -A && git commit -m "PromptBro 0.2.0"
```

then push that branch as `main`.
