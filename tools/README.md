# tools

One `<tool>.yaml` file per parsing tool. Keeping each tool in its own file makes it easy to compare setups and open pull requests without merge conflicts.

Each file lists the tool's setups. A setup has:

- `vendor` and `variant` — Who runs it and what sets it apart, shown on the board.
- `settings` — Every option sent to the tool. Changing one makes the harness parse every contract again.
- `tracked_changes_opt_in` — Whether the setup has to ask the tool to keep tracked changes.
- `bypasses_vendor_cache` — Whether the tool keeps its own result cache, which repeat parses must skip.
- `pricing` — List price per page, per credit, or per million tokens. A price the tool reports in its own response wins over this one.

To add or update a tool: edit `tools/<tool>.yaml`, run `.venv/bin/pytest`, and open a pull request. A new tool also needs `harness/adapters/<tool>.py` and one line in `harness/tool_registry.py`.
