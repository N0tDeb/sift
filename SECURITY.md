# Security policy

Sift inspects files that may contain untrusted or sensitive values. Reports,
terminal output and repair logs should not expose secrets or execute content
from those files. Please report security problems privately so they can be
investigated before public disclosure.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** option under the repository's
[Security advisories](https://github.com/N0tDeb/sift/security/advisories) if
private vulnerability reporting is enabled.

If that option is unavailable, open a
[public issue](https://github.com/N0tDeb/sift/issues/new) requesting a
private reporting channel **without posting exploit details, sensitive data,
or a proof of concept publicly**.

Include affected Sift versions, impact, a minimal safe reproducer and any
mitigations in the private report. Do not include genuine personal data.
The maintainer will triage the report and coordinate an appropriate fix and
disclosure. No guaranteed response time is promised.

## Supported versions

Sift is preparing its first public PyPI release. Before that release, security
fixes are made on `main`. Once stable releases are available, supported
version ranges will be listed here.

## Scope

Relevant reports include untrusted terminal or HTML output, leaks of sensitive
cell contents, unsafe file/path handling, unauthorized file modification,
spreadsheet-formula injection, and weaknesses in the package release process.

For ordinary incorrect findings or documentation problems, use the
[issue tracker](https://github.com/N0tDeb/sift/issues).
