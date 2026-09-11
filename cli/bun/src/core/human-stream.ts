import { humanSafeMultiline } from "./output";

const DEFAULT_MAXIMUM_CHARACTERS = 64 * 1024;
const TRUNCATION_NOTICE = "[OpenProse truncated harness output]";
const WITHHELD_NOTICE = "OpenProse completed; harness output was withheld by the human-output safety policy.\n";
const PARTIAL_WITHHELD_NOTICE = "OpenProse completed; the remaining harness output was withheld by the human-output safety policy.\n";

export interface HumanAssistantStreamOptions {
  write(text: string): void;
  protectedLiterals?: readonly string[];
  maximumCharacters?: number;
}

/**
 * Streams normalized assistant prose while retaining the last nonblank line.
 * The image contract reserves that line for its semantic terminal envelope,
 * so it is discarded only after the caller validates final settlement.
 */
export class HumanAssistantStream {
  private readonly write: (text: string) => void;
  private readonly protectedLiterals: readonly string[];
  private readonly maximumCharacters: number;
  private pending = "";
  private acceptedMessages = 0;
  private emittedCharacters = 0;
  private emitted = false;
  private truncated = false;
  private stalled = false;
  private finished = false;

  constructor(options: HumanAssistantStreamOptions) {
    this.write = options.write;
    this.protectedLiterals = [...(options.protectedLiterals ?? [])]
      .filter((value) => value.length > 0)
      .sort((left, right) => right.length - left.length);
    this.maximumCharacters = options.maximumCharacters ?? DEFAULT_MAXIMUM_CHARACTERS;
  }

  accept(text: string): void {
    if (this.finished || this.truncated || this.stalled) return;
    // The visible separator between two normalized assistant messages is
    // runner-owned. Check the raw boundary first so a harness cannot split a
    // protected literal across messages and disclose its two halves.
    if (this.protectedLiterals.some((literal) => crossesMessageBoundary(this.pending, text, literal))) {
      this.stalled = true;
      this.pending = "";
      return;
    }
    if (this.acceptedMessages > 0) this.pending += "\n";
    this.acceptedMessages += 1;
    this.pending += text;
    this.flushNonterminalPrefix();
  }

  /** Discards the validated control-terminal carrier and fills empty output. */
  completeSuccess(fallback: string): void {
    if (this.finished) return;
    this.finished = true;
    this.pending = "";
    if (this.stalled) {
      this.write(this.emitted ? PARTIAL_WITHHELD_NOTICE : WITHHELD_NOTICE);
    } else if (!this.emitted) {
      this.write(humanSafeMultiline(fallback));
    }
  }

  /** Drops all still-buffered content when transport or terminal validation fails. */
  abort(): void {
    this.finished = true;
    this.pending = "";
  }

  private flushNonterminalPrefix(): void {
    const safeEnd = startOfLastNonblankLine(this.pending);
    if (safeEnd <= 0) return;
    const safe = this.pending.slice(0, safeEnd);
    // Leading blank output is neither useful nor sufficient to suppress the
    // successful empty-output fallback. Keep it until substantive prose is safe.
    if (safe.trim().length === 0) return;
    this.pending = this.pending.slice(safeEnd);
    if (!safeHumanStreamText(safe, this.protectedLiterals)) {
      // Once an earlier prefix is withheld, later text must also be withheld.
      // Emitting it would reorder the harness response and could disclose a
      // continuation of the rejected control or protected value.
      this.stalled = true;
      this.pending = "";
      return;
    }
    this.emit(safe);
  }

  private emit(text: string): void {
    const safe = humanSafeMultiline(text);
    const remaining = this.maximumCharacters - this.emittedCharacters;
    if (remaining <= 0) return;
    if (safe.length <= remaining) {
      this.write(safe);
      this.emittedCharacters += safe.length;
      this.emitted = true;
      return;
    }
    this.write(`${safe.slice(0, remaining)}\n${TRUNCATION_NOTICE}\n`);
    this.emittedCharacters += remaining;
    this.emitted = true;
    this.truncated = true;
    this.pending = "";
  }
}

function startOfLastNonblankLine(value: string): number {
  let lineEnd = value.length;
  while (lineEnd > 0) {
    const lineStart = value.lastIndexOf("\n", lineEnd - 1) + 1;
    if (value.slice(lineStart, lineEnd).trim().length > 0) return lineStart;
    if (lineStart === 0) return 0;
    lineEnd = lineStart - 1;
  }
  return 0;
}

function crossesMessageBoundary(previous: string, current: string, literal: string): boolean {
  for (let split = 1; split < literal.length; split += 1) {
    if (previous.endsWith(literal.slice(0, split)) && current.startsWith(literal.slice(split))) {
      return true;
    }
  }
  return false;
}

function safeHumanStreamText(text: string, literals: readonly string[]): boolean {
  if (literals.some((literal) => text.includes(literal))) return false;
  return !text.split("\n").some((line) => {
    const trimmed = line.trim();
    // A fragmented or malformed control record is at least as unsafe as a
    // complete JSON object. Human-mode streaming therefore withholds every
    // line whose first non-space byte could begin an object or array.
    return trimmed[0] === "{" || trimmed[0] === "[";
  });
}
