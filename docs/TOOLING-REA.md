# Tooling: enabling REA decompilation

Written 2026-11-18. REA can inspect PE/ELF headers and imports without a
disassembler, which is how the vendor driver was characterised
([`FIRMWARE-PACKAGES.md`](FIRMWARE-PACKAGES.md)). Decompiling a firmware
package, or a driver whose protocol matters, needs a native provider.

## What REA accepts

The provider is pinned to a specific release pair. From `rea-agents@6.8.0`:

```
SUPPORTED_GHIDRA_VERSION = "12.1.4"
SUPPORTED_GHIDRA_JAVA_MAJOR = 21
```

Other 12.1.x builds are accepted and reported as `compatible`; a different minor
(12.2, say) is rejected as unsupported. Java 21 is the requirement — Ghidra 12.1.4
will not start on Java 25, which is what this machine has as its system JDK.

## What is installed here

| Component | Path |
|---|---|
| Ghidra 12.1.4 | `/home/wkipling/ghidra_12.1.4_PUBLIC` |
| Temurin JDK 21 | `/home/wkipling/jdk-21.0.13+9` |

Ghidra is pinned to that JDK inside its own launcher config, so nothing depends
on the environment:

```
# /home/wkipling/ghidra_12.1.4_PUBLIC/support/launch.properties
JAVA_HOME_OVERRIDE=/home/wkipling/jdk-21.0.13+9
```

REA finds the install through the `env` of its own server registration in Zed:

```json
"context_servers": {
  "rea": {
    "command": "npx",
    "args": ["-y", "rea-agents@6.8.0", "mcp"],
    "env": {
      "GHIDRA_INSTALL_DIR": "/home/wkipling/ghidra_12.1.4_PUBLIC"
    }
  }
}
```

A backup of the settings file was left at
`~/.config/zed/settings.json.bak-ghidra`.

## Applying it

Environment changes are read when the MCP server starts, so **Zed must be
restarted** (reloading the window is not enough). Confirm with `binary_session`:
the ghidra provider should report `available` and a version of 12.1.4.

Smoke test, independent of REA:

```
/home/wkipling/ghidra_12.1.4_PUBLIC/support/analyzeHeadless \
    /tmp/ghidra-smoke proj -import <file> -noanalysis
```

## Notes

- Switching to 12.2 is not an option until REA's pin moves; it needs Java 25 and
  is reported as an unsupported release line.
- The system JDK (25) must not be used for Ghidra 12.1.4. If the launcher ever
  says "unsupported java version", `JAVA_HOME_OVERRIDE` is the place to look.
- With the provider available, the useful queries on a vendor binary are
  `analyze_function` around the `HidD_SetFeature` call sites, and
  `procedure_pseudo_code` for the command builders.
