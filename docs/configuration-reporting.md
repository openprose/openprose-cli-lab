# Optional configuration fields

Configuration reports preserve their historical default shape. `outputContract` and `permissionMode` appear in configuration explanations and configuration provenance when selected by a flag, environment variable or configuration file. An explicit selection is reported even when its value equals the default. Both fields are omitted when their source is the built-in default.

This is a reporting rule only. The default output contract remains `image-envelope`; an unset permission mode continues to defer to the existing harness behavior. Reports do not grant permissions or change launch arguments. Named properties in the closed configuration schema describe the reported values and their source.

Both runners report the same selected values, source kind and canonical flag/environment names. Config-file location precision currently differs: Rust records the file path, while Bun records the file path and line. This existing difference is retained rather than discarding Bun's line evidence. Default and explicit-flag reports have equal parsed JSON values; file-origin reports retain that documented location difference.
