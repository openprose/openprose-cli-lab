#!/usr/bin/env bun

const capturePath = process.env.LAB_CAPTURE_PATH;
if (capturePath === undefined) {
  process.stderr.write("LAB_CAPTURE_PATH is required\n");
  process.exit(2);
}

const input = await Bun.stdin.text();
await Bun.write(
  capturePath,
  JSON.stringify({
    argv: process.argv.slice(2),
    env: {
      CODEX_HOME: process.env.CODEX_HOME,
      HOME: process.env.HOME,
      LAB_SECRET_SHOULD_BE_ABSENT: process.env.LAB_SECRET_SHOULD_BE_ABSENT,
    },
    input,
  }),
);

const mode = process.env.LAB_FAKE_MODE ?? "success";
process.stdout.write(
  `${JSON.stringify({ type: "thread.started", thread_id: "fake-thread" })}\n`,
);
process.stdout.write(
  `${JSON.stringify({
    type: "item.completed",
    item: { id: "message-1", type: "agent_message", text: "fake response" },
  })}\n`,
);

if (mode === "delay") {
  await Bun.sleep(30_000);
} else if (mode === "failure") {
  process.stdout.write(
    `${JSON.stringify({ type: "turn.failed", error: { message: "fake failure" } })}\n`,
  );
} else if (mode !== "no-terminal") {
  process.stdout.write(
    `${JSON.stringify({
      type: "turn.completed",
      usage: {
        cached_input_tokens: 0,
        input_tokens: 1,
        output_tokens: 1,
      },
    })}\n`,
  );
}
