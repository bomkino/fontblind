# Branch policy

- `main` is the only permanent product branch and the default branch.
- Product work uses short-lived topic branches and pull requests.
- A branch may merge only after exact-head automated verification passes and the diff still matches the intended scope.
- No branch name, pull request title, status document, or timestamp outranks commit ancestry, patch equivalence, current files, and exact workflow evidence.
- Squash merge is preferred for noisy autonomous-agent histories; coherent commit sequences may use a merge commit.
- Fully merged, squash-equivalent, duplicate, superseded, diagnostic, transfer, and abandoned branches are deleted after their tip SHA and disposition are recorded.
- Unique rejected work is either reduced to the useful subset, documented and retained temporarily against an issue, or archived deliberately. Branch clutter is not archival policy.
- `main` must never be force-pushed or deleted.
