#!/usr/bin/env bun

import { defaultDependencies, runCli } from "./cli";

process.exitCode = await runCli(process.argv.slice(2), defaultDependencies());
