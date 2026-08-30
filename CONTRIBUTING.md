# Contributing

1. Fork the repository and create a focused branch.
2. Keep the project dependency-free unless a dependency adds clear value.
3. Add or update unit tests for behavior changes.
4. Run:

   ```bash
   python3 -m unittest discover -s tests -v
   python3 -m compileall -q scripts tests
   ```

5. Do not include real bot tokens, chat IDs, Codex task IDs, local usernames,
   absolute personal paths, OTPs, or private notification payloads.

Bug reports should include sanitized reproduction steps and the macOS, Codex,
and Python versions involved.
