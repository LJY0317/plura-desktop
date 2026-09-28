## Summary

Describe the user-visible or contract-level change and why it belongs in Plura Desktop.

## Validation

- [ ] `python3 -m unittest discover -s tests -v`
- [ ] `python3 -m compileall -q src scripts tests`
- [ ] POSIX/PowerShell wrapper syntax checks as applicable
- [ ] `python3 scripts/check_repository_invariants.py`
- [ ] Real official-app verification is identified separately from fake-executable/CI coverage when platform launch behavior changes

## Safety and ownership

- [ ] The official ChatGPT application remains immutable/read-only
- [ ] Managed profile state remains isolated and no auth/cookie/profile database is copied between profiles
- [ ] Destructive paths remain exact, revalidated manifest-owned targets
- [ ] The change does not add an idle poller/watcher/background sampler without an explicit demonstrated need
- [ ] Public target/session contracts do not expose private profile paths, fixed private ports, or credentials

## Privacy

- [ ] Logs, screenshots, fixtures, and examples contain no credentials, cookies, conversation content, raw profile databases, private user paths, or other sensitive data
