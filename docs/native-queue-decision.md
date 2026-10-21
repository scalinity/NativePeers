# Native queue decision

Use the managed native queue. No Codex inbox patch is selected.

Locally verified probe evidence: [M1 observations](../evidence/m1-native-observations.json). The ordinary configured TUI and an explicitly standalone `--no-daemon` TUI both consumed helper submissions in the original native UUID and returned their exact tags. The sender used ordinary `codex queue` without `--no-daemon`. Both parents launched helpers from actual shell tools; detached helpers survived and cleaned their lease children.

The installed upstream source separately contains the ten-second external watcher and the explicit sender no-daemon guard. Source inspection alone was not treated as live delivery evidence. Final-product busy/draft/correlation acceptance remains NOT_RUN. Private Desktop/T3 ingress is unverified. Native queue admission does not establish ingestion, local expiry, or exactly-once execution.
