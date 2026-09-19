# Driving capsule-corp from an MCP client

capsule-corp speaks the Model Context Protocol, so any MCP client can browse and drive
the catalogue — not only the agent that writes the capsules.

```bash
capsule mcp
```

That serves the catalogue over stdio. Point a client at it by adding a server entry; in
Claude Code, for example:

```json
{
  "mcpServers": {
    "capsule-corp": {
      "command": "capsule",
      "args": ["mcp"],
      "cwd": "/path/to/your/catalogue"
    }
  }
}
```

## Tools

| Tool                | What it does                                                         |
| ------------------- | -------------------------------------------------------------------- |
| `capsule_list`      | List capsules, optionally filtered by folder or status               |
| `capsule_search`    | Full-text search over titles, questions, and reports                 |
| `capsule_get`       | One capsule in full: pre-registration, results, verification, report |
| `capsule_new`       | Create a skeleton (does not call a model)                            |
| `capsule_design`    | Run the design phase — **spends money**                              |
| `capsule_freeze`    | Lock the pre-registration — **irreversible**                         |
| `capsule_implement` | Run the implementation phase — **spends money**                      |
| `capsule_run`       | Execute on `local`, `slurm`, `ssh`, or `modal`                       |
| `capsule_verify`    | Deterministic checks, then the blinded judge                         |

## Resources

- `capsule://index` — every capsule with its current status.
- `capsule://<id>` — one capsule in full.

The resources are the part worth caring about. They let an agent ask _"what have I
already tested here, and what did it show?"_ before spending compute on a question the
catalogue has already answered — including the ones whose hypotheses were refuted,
which are exactly the results that never get written up anywhere else.

## Going the other way: giving capsules MCP tools

`pi` has no built-in MCP support; it is deliberately minimal and pushes MCP, sandboxing
and sub-agents out to extensions. The third-party
[`pi-mcp-adapter`](https://www.npmjs.com/package/pi-mcp-adapter) provides it:

```bash
npm install -g pi-mcp-adapter
```

`capsule doctor` reports whether it is installed. Track
[earendil-works/pi#563](https://github.com/earendil-works/pi/issues/563) for an official
path.
