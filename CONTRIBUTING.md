# Contributing

Thank you for helping. A few rules keep the project open and safe:

1. **Licence.** Contributions are under AGPL-3.0-or-later, the project's licence.
2. **Sign-off (DCO).** Add `Signed-off-by: Your Name <you@example.org>` to each commit (`git commit -s`) to certify the [Developer Certificate of Origin](https://developercertificate.org/). No CLA.
3. **Tests.** `PYTHONPATH=src python -m unittest discover -s tests -t .` must pass. New behaviour comes with a test; a safety rule comes with a test that would fail if the rule were removed.
4. **No organization specifics in code.** Addresses, names, logos and secrets of any operator live in configuration, never in code, tests or examples (use `example.com` / `example.org`). `ci/name-check` enforces it with a private word list.
5. **Words people read** follow `DESIGN.md`: fewest words, examples inside the boxes, no raw identifiers, English, French and German together.
6. **Security problems** go to the private contact in `SECURITY.md`, not to a public issue.
