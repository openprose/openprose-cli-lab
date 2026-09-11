# OMP asynchronous task progress

OMP18.0.9 can return an initial task tool result while its native background job continues. Its TaskTool explicitly retains onUpdate callbacks for job progress after return. A tool result declaring details.async {type: task, state: running, jobId: nonempty string}, with isError false, establishes the correlation for later updates.

The adapter retains that task ID, admitted arguments and job ID within the existing bounded invocation record stream. Before parent terminal, a later tool_execution_update is admitted only for the same task tool, matching arguments and job, and native async state running/completed/failed. It remains an observation: it does not reopen the tool, rewrite its initial result or message history, or establish child acceptance or parent completion. Other tools and asynchronous shapes are unchanged. Progress after terminal and duplicate tool completion remain invalid.

The installed TaskTool source documents detached jobs and its onProgress callback. The observed failure has task end at record2408 with job IndependentReview running, followed by a matching progress update2413 after the next turn begins. Original failure remains retained. Full-command provider-free tests use that unchanged prefix with an explicitly synthetic valid completion and reject wrong job/arguments, absent async admission, wrong tool, duplicate completion and postterminal updates.

Native asynchronous execution may differ from a program's requested synchronous procedure; this adapter does not decide that program-level question. Rust may report cleanup failure rather than the narrower rejected/incomplete-stream diagnosis in some fake-process negative tests. Those cases remain failures with no successful terminal, not claimed diagnostic parity.
