# Isolated evaluation profile

A fresh CODEX_HOME is insufficient to remove ambient Codex skills. The pinned native agent also discovers the user's `.agents/skills` directory. In one real run it loaded a previous interpreter from that directory despite having the current workspace README. Correct output from that run cannot establish standalone behavior of the current Markdown library.

Use an explicit evaluation launcher that passes native `skills.config` disable entries for the discovered SKILL.md paths and disables native `plugins` and `skill_search`. The lab's opt-in snapshot is `lab/tools/codex-isolated/codex`, with settings and provider-free `debug prompt-input` evidence beside it. Ordinary product defaults remain unchanged. This launcher changes native configuration only; it passes task arguments through and never adds instructions about the language.

The native binary accepted individual SKILL.md paths reliably; folder-only entries did not remove all skills from the full catalog. This matches the example in [official skill configuration guidance](https://learn.chatgpt.com/docs/build-skills). Keep both lexical and resolved paths when inventorying linked skills, inspect the resulting native prompt, and refresh the snapshot when installed skills change.

This is controlled instruction discovery, not OS isolation. The agent can still read files available under its ordinary sandbox. Same-run native tool evidence must be inspected for unexpected source loading. The previous contaminated run is retained, and a clean run uses a separate directory and frozen manifest.

Native option scope matters: pinned Codex replaces root-level `-c` overrides when an exec subcommand supplies its own list. The isolated launcher therefore inserts settings inside the exec option scope alongside provider settings. A root-only debug prompt was insufficient proof of the actual exec path. A matched live native inventory probe confirms that only the five fresh-home built-in system skills remain; user skills, including the previous language, are absent. The first attempted isolated cohort is retained as contaminated evidence of this native configuration behavior.
