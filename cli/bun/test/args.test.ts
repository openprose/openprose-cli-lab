import { describe, expect, test } from "bun:test";
import { inferOutputMode, parseEntrypoint } from "../src/core/args";

const invocationAction = "Review the runner syntax with the --help option, place global options before cli, and retry the command.";

function expectInvocationFailure(args: readonly string[], reason: string): void {
  try {
    parseEntrypoint(args);
    throw new Error("expected runner invocation to fail");
  } catch (error) {
    expect(error).toMatchObject({
      code: "INVOCATION_INVALID",
      boundary: "invocation",
      message: "Runner invocation is invalid.",
      action: invocationAction,
      exitCode: 2,
      retryable: false,
      details: { reason },
    });
  }
}

describe("runner-global parsing", () => {
  test("freezes forever at the first language token", () => {
    expect(parseEntrypoint(["--harness", "mock", "run", "--harness", "claude", "--model=x"]))
      .toMatchObject({
        kind: "language",
        global: { harness: "mock" },
        argv: ["prose", "run", "--harness", "claude", "--model=x"],
      });
  });

  test.each([
    ["Unicode and whitespace", ["write", "雪 ❄️", "line\nfeed", "\t"]],
    ["leading dash", ["--future-language-option", "value"]],
    ["shell metacharacters", ["run", "$(touch nope)", "`whoami`", "a;b", "x|y", "*", "'quoted'"]],
  ])("preserves %s as exact opaque argv", (_label, tail) => {
    expect(parseEntrypoint(tail)).toMatchObject({
      kind: "language",
      argv: ["prose", ...tail],
    });
  });

  test("the delimiter forces the reserved cli noun through to the language", () => {
    expect(parseEntrypoint(["--", "cli", "doctor", "--json"])).toEqual({
      kind: "language",
      global: {},
      argv: ["prose", "cli", "doctor", "--json"],
    });
  });

  test("a bare delimiter is rejected instead of inventing a language command", () => {
    expectInvocationFailure(["--"], "`--` must be followed by a language command");
  });

  test("malformed runner syntax uses the invocation boundary", () => {
    for (const [args, reason] of [
      [["--output=machine", "cli", "doctor"], 'invalid output mode "machine"; expected human, json, or jsonl'],
      [["--model", "one", "--model", "two", "cli", "doctor"], "runner option --model was specified more than once"],
      [["cli", "doctor", "extra"], "Unknown runner operation: cli doctor extra."],
      [["cli", "harness", "use", "nope"], "Harness selection must be one of openprose, prime, omp, codex, or claude."],
    ] as const) {
      expectInvocationFailure(args, reason);
    }
  });

  test("unescaped cli is runner-owned", () => {
    expect(parseEntrypoint(["--harness=mock", "cli", "config", "explain", "--json"]))
      .toMatchObject({ kind: "operation", operation: "config-explain", json: true });
  });

  test("parses auth profile as a runner-global value and output inference skips it", () => {
    expect(parseEntrypoint([
      "--harness", "prime", "--auth-profile=openrouter", "--model", "fixture-model", "run",
    ])).toMatchObject({
      kind: "language",
      global: { harness: "prime", authProfile: "openrouter", model: "fixture-model" },
      argv: ["prose", "run"],
    });
    for (const auth of [["--auth-profile", "openrouter"], ["--auth-profile=openrouter"]]) {
      expect(inferOutputMode([...auth, "--output", "json", "run"])).toBe("json");
    }
  });

  test("rejects empty or missing auth profile values", () => {
    for (const args of [["--auth-profile="], ["--auth-profile"]]) {
      expect(() => parseEntrypoint(args)).toThrow("Runner invocation is invalid.");
    }
  });

  test("parses an explicit user harness selection without crossing the language boundary", () => {
    expect(parseEntrypoint(["--output", "json", "cli", "harness", "use", "claude"]))
      .toEqual({
        kind: "operation",
        global: { output: "json" },
        operation: "harness-use",
        value: "claude",
        json: false,
      });
    expect(() => parseEntrypoint(["cli", "harness", "use", "mock"]))
      .toThrow("Runner invocation is invalid.");
  });

  test("parses harness-selection model and auth flags before or after the runner command", () => {
    expect(parseEntrypoint([
      "cli", "harness", "use", "prime",
      "--model", "openai/gpt-5.4",
      "--auth-profile=prime-harness-login",
      "--json",
    ])).toEqual({
      kind: "operation",
      global: { model: "openai/gpt-5.4", authProfile: "prime-harness-login" },
      operation: "harness-use",
      value: "prime",
      json: true,
    });
    expect(parseEntrypoint([
      "--model=openai/gpt-5.4", "--auth-profile", "prime-harness-login",
      "cli", "harness", "use", "prime",
    ])).toEqual({
      kind: "operation",
      global: { model: "openai/gpt-5.4", authProfile: "prime-harness-login" },
      operation: "harness-use",
      value: "prime",
      json: false,
    });
  });

  test("rejects duplicate or conflicting harness-selection route flags", () => {
    for (const args of [
      ["--model", "openai/first", "cli", "harness", "use", "prime", "--model", "openai/second"],
      ["cli", "harness", "use", "prime", "--model=openai/first", "--model=openai/first"],
      ["--auth-profile", "openai", "cli", "harness", "use", "prime", "--auth-profile", "openrouter"],
      ["cli", "harness", "use", "prime", "--auth-profile=openai", "--auth-profile=openai"],
      ["cli", "harness", "use", "prime", "--json", "--json"],
      ["--model", "openai/first", "--model", "openai/second", "cli", "harness", "use", "prime"],
      ["--auth-profile=openai", "--auth-profile=openai", "cli", "harness", "use", "prime"],
    ]) {
      expect(() => parseEntrypoint(args)).toThrow("Runner invocation is invalid.");
    }
  });

  test.each([
    ["--model", ["cli", "harness", "use", "prime", "--model=openai/first", "--model=openai/second"]],
    ["--auth-profile", ["--auth-profile", "openai", "cli", "harness", "use", "prime", "--auth-profile", "openrouter"]],
  ] as const)("uses the canonical duplicate %s selection error", (option, args) => {
    try {
      parseEntrypoint(args);
      throw new Error("expected duplicate selection option to fail");
    } catch (error) {
      expect(error).toMatchObject({
        code: "INVOCATION_INVALID",
        boundary: "invocation",
        message: "Runner invocation is invalid.",
        action: invocationAction,
        exitCode: 2,
        retryable: false,
        details: { reason: `runner option ${option} was specified more than once` },
      });
    }
  });

  test("infers JSON for a malformed runner command with a trailing local flag", () => {
    expect(inferOutputMode([
      "--model=openai/gpt-5.4",
      "cli", "harness", "use", "prime",
      "--model", "openai/gpt-5.4",
      "--json",
    ])).toBe("json");
    expect(inferOutputMode([
      "--", "cli", "harness", "use", "prime", "--json",
    ])).toBe("human");
    expect(inferOutputMode([
      "run", "cli", "harness", "use", "prime", "--json",
    ])).toBe("human");
  });

  test("parses Prime cleanup only through the global output surface", () => {
    const handle = "prime-v1.openprose-prime-Fixture1.018f47a6-7d2c-7b10-8a2e-1a2b3c4d5e6f";
    expect(parseEntrypoint(["--output", "json", "cli", "cleanup", "prime", handle])).toEqual({
      kind: "operation",
      global: { output: "json" },
      operation: "prime-cleanup",
      value: handle,
      json: false,
    });
    expect(() => parseEntrypoint(["cli", "cleanup", "prime", handle, "--json"]))
      .toThrow("Runner invocation is invalid.");
  });

  test.each([
    ["status", "auth-status"],
    ["login", "auth-login"],
    ["logout", "auth-logout"],
  ] as const)("recognizes the hosted account %s operation", (command, operation) => {
    expect(parseEntrypoint(["--output", "json", "cli", "auth", command]))
      .toEqual({
        kind: "operation",
        global: { output: "json" },
        operation,
        json: false,
      });
  });

  test("known runner groups and commands accept help without configuration", () => {
    for (const args of [
      ["cli", "--help"],
      ["cli", "doctor", "--help"],
      ["cli", "harness", "--help"],
      ["cli", "harness", "list", "--help"],
      ["cli", "harness", "use", "--help"],
      ["cli", "harness", "use", "prime", "--help"],
      ["cli", "cleanup", "--help"],
      ["cli", "cleanup", "prime", "--help"],
      ["cli", "cleanup", "prime", "opaque-handle", "--help"],
      ["cli", "config", "--help"],
      ["cli", "config", "explain", "--help"],
      ["cli", "auth", "--help"],
      ["cli", "auth", "status", "--help"],
      ["cli", "auth", "login", "--help"],
      ["cli", "auth", "logout", "--help"],
    ]) {
      expect(parseEntrypoint(args)).toEqual({ kind: "help", global: {} });
    }
  });

  test("help does not make unknown runner paths valid", () => {
    for (const args of [
      ["cli", "unknown", "--help"],
      ["cli", "harness", "unknown", "--help"],
      ["cli", "doctor", "extra", "--help"],
    ]) {
      expect(() => parseEntrypoint(args)).toThrow("Runner invocation is invalid.");
    }
  });
});
test("permission mode is an explicit runner flag",()=>{
 const parsed=parseEntrypoint(["--permission-mode","acceptEdits","run","PROGRAM.md"]);
 expect(parsed.global.permissionMode).toBe("acceptEdits");
});

test("native output is an explicit transport option",()=>{
 expect(parseEntrypoint(["--output-contract","native","run","a.md"])).toMatchObject({global:{outputContract:"native"}});
 expect(()=>parseEntrypoint(["--output-contract","guessed","run"])).toThrow();
});
