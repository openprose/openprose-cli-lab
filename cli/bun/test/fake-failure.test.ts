import { expect, test } from "bun:test";
import { failure } from "../src/core/errors";
import { fakeProtocolFailureDetails } from "../src/supervision/fake-failure";

test("fake transport preserves bounded framing evidence without echoing arbitrary errors", () => {
  const diagnostic = { schema: "openprose.transport-diagnostic/1", reason: "invalid-json", observedBytes: 7 };
  expect(fakeProtocolFailureDetails({ error: failure("PROTOCOL_MALFORMED", { transportDiagnostic: diagnostic, reason: "untrusted text" }), events: [] })).toEqual({ reason: "invalid_json", admittedRecordCount: 0, transportDiagnostic: diagnostic });
  expect(fakeProtocolFailureDetails({ error: failure("PROTOCOL_TRUNCATED", { reason: "untrusted text" }), events: [] })).toEqual({ reason: "protocol_admission_rejected", admittedRecordCount: 0, transportDiagnostic: { schema: "openprose.transport-diagnostic/1", reason: "lifecycle-rejection" } });
  expect(fakeProtocolFailureDetails({ error: failure("HARNESS_FAILED"), events: [] })).toEqual({});
});
