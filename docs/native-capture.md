# Native evidence capture

`--native-log /absolute/new/file.jsonl` opts into a private native-event artifact for the same execution. The parent directory must exist and the file must not already exist. Both runners create it with mode 0600 on Unix, append JSON records before semantic normalization, and stop the run if the capture exceeds 64 MiB or cannot be written. `native_log` and `PROSE_NATIVE_LOG` also select this path. Default is off.

Capture records are parsed native JSON events, not byte-for-byte stdout; malformed JSON bytes cannot be represented and are not captured. The artifact may end without native completion after a failure. Native protocol validation still decides whether the process settles. Capturing an event does not admit it or establish program fulfillment.

Known selected credential values and runner control secrets are replaced in string values. This is not a general redaction guarantee: tools may read sensitive files, tool arguments/results may contain other secrets, and split or encoded values may escape replacement. Treat this as a private sensitive artifact. Use synthetic fixtures for shared evidence. No capture file is uploaded automatically.
