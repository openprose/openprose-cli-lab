#!/bin/sh
set -eu

LAB_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOLCHAIN=1.90.0
CARGO_BIN=$(rustup which cargo --toolchain "$TOOLCHAIN")
RUSTC_BIN=$(rustup which rustc --toolchain "$TOOLCHAIN")
CARGO_CLIPPY_BIN=$(rustup which cargo-clippy --toolchain "$TOOLCHAIN")
CLIPPY_DRIVER_BIN=$(rustup which clippy-driver --toolchain "$TOOLCHAIN")
CARGO_FMT_BIN=$(rustup which cargo-fmt --toolchain "$TOOLCHAIN")
RUSTFMT_BIN=$(rustup which rustfmt --toolchain "$TOOLCHAIN")

cd "$LAB_ROOT"
RUSTFMT="$RUSTFMT_BIN" "$CARGO_FMT_BIN" fmt --all -- --check
RUSTC="$RUSTC_BIN" RUSTC_WORKSPACE_WRAPPER="$CLIPPY_DRIVER_BIN" \
  "$CARGO_CLIPPY_BIN" clippy --workspace --all-targets --locked --offline -- -D warnings
RUSTC="$RUSTC_BIN" "$CARGO_BIN" test --workspace --all-targets --locked --offline
RUSTC="$RUSTC_BIN" "$CARGO_BIN" build --workspace --release --locked --offline
