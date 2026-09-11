import { describe, expect, test } from "bun:test";
import { HumanAssistantStream } from "../src/core/human-stream";

describe("human assistant streaming", () => {
  test("emits only lines that can no longer carry the image terminal", () => {
    let output = "";
    const stream = new HumanAssistantStream({ write: (text) => { output += text; } });

    stream.accept("first visible line\nsecond visible line\n{terminal-candidate}");
    expect(output).toBe("first visible line\nsecond visible line\n");
    expect(output).not.toContain("terminal-candidate");

    stream.completeSuccess("unused fallback\n");
    expect(output).toBe("first visible line\nsecond visible line\n");
  });

  test("retains one candidate and stops before protected values without reordering output", () => {
    let output = "";
    const stream = new HumanAssistantStream({
      write: (text) => { output += text; },
      protectedLiterals: ["credential-value", "raw-task-json"],
    });

    stream.accept("first\nformer candidate credential-value");
    expect(output).toBe("first\n");
    stream.accept("raw-task-json is now visible\n{actual-terminal}");
    stream.completeSuccess("OpenProse completed.\n");
    expect(output).toBe([
      "first",
      "OpenProse completed; the remaining harness output was withheld by the human-output safety policy.",
      "",
    ].join("\n"));
    expect(output).not.toContain("credential-value");
    expect(output).not.toContain("raw-task-json");
    expect(output).not.toContain("actual-terminal");
  });

  test("never emits completed control JSON or later text", () => {
    let output = "";
    const stream = new HumanAssistantStream({ write: (text) => { output += text; } });

    stream.accept("safe prose\n{\"schema\":\"unsettled-control\"}");
    expect(output).toBe("safe prose\n");
    stream.accept("later prose must remain withheld\n{terminal}");
    stream.completeSuccess("unused fallback\n");
    expect(output).toBe([
      "safe prose",
      "OpenProse completed; the remaining harness output was withheld by the human-output safety policy.",
      "",
    ].join("\n"));
  });

  test("detects split control JSON and protected fragments before first output", () => {
    let controlOutput = "";
    const control = new HumanAssistantStream({ write: (text) => { controlOutput += text; } });
    control.accept("{\"schema\":");
    control.accept("\"split-control\"}\n{terminal}");
    control.completeSuccess("OpenProse completed.\n");
    expect(controlOutput).toBe("OpenProse completed; harness output was withheld by the human-output safety policy.\n");

    let protectedOutput = "";
    const protectedStream = new HumanAssistantStream({
      write: (text) => { protectedOutput += text; },
      protectedLiterals: ["runner-selected-model"],
    });
    protectedStream.accept("runner-selected-");
    protectedStream.accept("model\n{terminal}");
    protectedStream.completeSuccess("OpenProse completed.\n");
    expect(protectedOutput).toBe("OpenProse completed; harness output was withheld by the human-output safety policy.\n");
  });

  test("does not discard short task literals from the protection set", () => {
    let output = "";
    const stream = new HumanAssistantStream({
      write: (text) => { output += text; },
      protectedLiterals: ["-p"],
    });
    stream.accept("attempted -p option\n{terminal}");
    stream.completeSuccess("unsafe attempted -p option\n");
    expect(output).toBe(
      "OpenProse completed; harness output was withheld by the human-output safety policy.\n",
    );
    expect(output).not.toContain("-p");
  });

  test("drops pending content on failure and bounds streamed human output", () => {
    let failedOutput = "";
    const failed = new HumanAssistantStream({ write: (text) => { failedOutput += text; } });
    failed.accept("safe\nmalformed terminal");
    failed.abort();
    failed.completeSuccess("must-not-appear\n");
    expect(failedOutput).toBe("safe\n");

    let boundedOutput = "";
    const bounded = new HumanAssistantStream({
      write: (text) => { boundedOutput += text; },
      maximumCharacters: 8,
    });
    bounded.accept("1234567890\n{terminal}");
    bounded.accept("must-not-stream\n{other-terminal}");
    bounded.completeSuccess("must-not-appear\n");
    expect(boundedOutput).toBe("12345678\n[OpenProse truncated harness output]\n");
    expect(boundedOutput).not.toContain("must-not-stream");
  });

  test("uses the completion fallback when no substantive nonterminal text exists", () => {
    let output = "";
    const stream = new HumanAssistantStream({ write: (text) => { output += text; } });
    stream.accept("\n\n{terminal}");
    expect(output).toBe("");
    stream.completeSuccess("OpenProse completed.\n");
    expect(output).toBe("OpenProse completed.\n");
  });

  test("preserves ordinary prose lines and visibly escapes hostile terminal controls", () => {
    let output = "";
    const stream = new HumanAssistantStream({ write: (text) => { output += text; } });
    stream.accept("first \\ path\nOSC:\u001b]0;owned\u0007\tend\u2028next\u2029paragraph\n{terminal}");
    expect(output).toBe(
      "first \\\\ path\nOSC:\\u{001B}]0;owned\\u{0007}\\tend\\u{2028}next\\u{2029}paragraph\n",
    );
    stream.completeSuccess("unused fallback\n");
    expect(output.split("\n")).toHaveLength(3);
    expect(output).not.toContain("\u001b");
    expect(output).not.toContain("\u0007");
    expect(output).not.toContain("\t");
    expect(output).not.toContain("\u2028");
    expect(output).not.toContain("\u2029");

    let fallback = "";
    const empty = new HumanAssistantStream({ write: (text) => { fallback += text; } });
    empty.completeSuccess("ordinary line\nhostile \\ \u001b\u0007\r\t\u0085\u2028\u2029\n");
    expect(fallback).toBe(
      "ordinary line\nhostile \\\\ \\u{001B}\\u{0007}\\r\\t\\u{0085}\\u{2028}\\u{2029}\n",
    );
  });
});
